"""LinkedIn guest crawler: parsing (trimmed real fragments) and polite fetching."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.crawlers.base import CrawlContext, FetchError, RawPage, RawPost, SourceInfo
from app.crawlers.linkedin import (
    DETAIL_URL,
    SEARCH_URL,
    Blocked,
    LinkedinCrawler,
    normalize_salary,
    search_job_ids,
)
from app.pipeline.ingest import build_row, merge_raw_posts

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
SEARCH = (FIXTURES / "search.html").read_text(encoding="utf-8")
DETAIL = (FIXTURES / "job_4487894954.html").read_text(encoding="utf-8")
FETCHED_AT = datetime(2026, 11, 17, 9, 40, tzinfo=UTC)
JOB_ID = "4487894954"


def search_page(body: str = SEARCH) -> RawPage:
    return RawPage(url=SEARCH_URL + "?keywords=x", body=body, fetched_at=FETCHED_AT)


def detail_page(job_id: str = JOB_ID, body: str = DETAIL) -> RawPage:
    return RawPage(url=DETAIL_URL.format(job_id=job_id), body=body, fetched_at=FETCHED_AT)


# --- parsing -------------------------------------------------------------------------------


def test_parse_search_cards() -> None:
    posts = LinkedinCrawler().parse(search_page())
    assert [p.external_id for p in posts] == search_job_ids(SEARCH)
    assert len(posts) == 3
    first = posts[0]
    assert first.external_id == JOB_ID
    assert first.title == "Senior .NET Developer (GSM platform)"
    assert first.company == "VinSmart Future"
    assert first.location_raw == "Hanoi, Hanoi, Vietnam"
    assert first.url == f"https://www.linkedin.com/jobs/view/{JOB_ID}"
    assert first.posted_at == FETCHED_AT - timedelta(hours=1)  # "1 hour ago"


def test_parse_detail() -> None:
    (post,) = LinkedinCrawler().parse(detail_page())
    assert post.external_id == JOB_ID
    assert post.title == "Senior .NET Developer (GSM platform)"
    assert post.company == "VinSmart Future"
    assert post.body.startswith("Company Description")
    assert "Show more" not in post.body[-20:]
    assert post.extra["levels"] == ["mid", "senior"]  # "Mid-Senior level"
    assert post.extra["employment_type"] == "FULL_TIME"


def test_detail_without_id_in_url_fails() -> None:
    with pytest.raises(ValueError):
        LinkedinCrawler().parse(RawPage(url="https://example.com/x", body=DETAIL))


def test_card_and_detail_merge_into_one_job_post() -> None:
    crawler = LinkedinCrawler()
    merged = merge_raw_posts(crawler.parse(search_page()) + crawler.parse(detail_page()))
    post = next(p for p in merged if p.external_id == JOB_ID)
    row = build_row(1, "job", post)
    assert row["title"] == "Senior .NET Developer (GSM platform)"
    assert row["location"] == "hanoi"
    assert row["body"].startswith("Company Description")
    assert row["attributes"]["levels"] == ["senior"]  # from the title, not the criteria
    assert row["attributes"]["employment_type"] == "FULL_TIME"
    assert "net" in row["attributes"]["skills"] or "dotnet" in row["attributes"]["skills"]


def test_seniority_is_the_fallback_when_the_title_has_no_level() -> None:
    raw = RawPost(external_id="1", url="u", title="Java Developer", extra={"levels": ["junior"]})
    assert build_row(1, "job", raw)["attributes"]["levels"] == ["junior"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,000.00 - $2,000.00", "1000 - 2000 USD"),
        ("$60K/yr - $90K/yr", "5000 - 7500 USD"),
        ("VND 20,000,000.00/mo", "20000000 VND"),
        ("$10/hr", "1600 USD"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_salary(text, expected) -> None:
    assert normalize_salary(text) == expected


# --- fetching ------------------------------------------------------------------------------


class FakeFetcher:
    """Serves search pages from `results[keyword]` (lists of id lists) and detail pages."""

    def __init__(self, results: dict[str, list[list[str]]], fail: dict | None = None) -> None:
        self.results = results
        self.fail = fail or {}
        self.calls: list[tuple[str, dict | None]] = []

    @staticmethod
    def _cards(ids: list[str]) -> str:
        return "".join(
            f'<li><div class="base-card" data-entity-urn="urn:li:jobPosting:{i}">'
            f'<h3 class="base-search-card__title">Job {i}</h3></div></li>'
            for i in ids
        )

    def get(self, url: str, *, params: dict | None = None) -> RawPage:
        self.calls.append((url, params))
        key = (params or {}).get("start") if url == SEARCH_URL else url.rsplit("/", 1)[-1]
        if key in self.fail:
            raise self.fail[key]
        if url == SEARCH_URL:
            pages = self.results.get(params["keywords"], [])
            index = params["start"] // 10
            body = self._cards(pages[index]) if index < len(pages) else ""
            return RawPage(url=f"{url}?start={params['start']}", body=body)
        return RawPage(url=url, body=DETAIL)


def run_fetch(fetcher: FakeFetcher, max_posts: int = 300, **config) -> list[RawPage]:
    ctx = CrawlContext(
        run_id=1,
        source=SourceInfo(1, "linkedin", "LinkedIn", config),
        fetcher=fetcher,
        max_posts=max_posts,
    )
    return list(LinkedinCrawler().fetch(ctx))


def test_fetch_paginates_dedupes_across_keywords_and_caps_details() -> None:
    fetcher = FakeFetcher(
        {
            "python": [["1", "2"], ["3"]],
            "developer": [["3", "4"]],
        }
    )
    pages = run_fetch(fetcher, keywords=["python", "developer"], max_details_per_run=3)

    search_calls = [p for u, p in fetcher.calls if u == SEARCH_URL]
    assert [(p["keywords"], p["start"]) for p in search_calls] == [
        ("python", 0),
        ("python", 10),
        ("python", 20),  # empty: end of results
        ("developer", 0),
        ("developer", 10),
    ]
    assert search_calls[0]["f_TPR"] == "r86400" and search_calls[0]["location"] == "Vietnam"
    detail_urls = [u for u, _ in fetcher.calls if u != SEARCH_URL]
    assert detail_urls == [DETAIL_URL.format(job_id=i) for i in ("1", "2", "3")]
    assert len(pages) == 3 + 3  # three non-empty search pages, three details


def test_fetch_respects_max_posts() -> None:
    fetcher = FakeFetcher({"": [["1", "2"], ["3", "4"], ["5"]]})
    run_fetch(fetcher, max_posts=3, max_details_per_run=0)
    assert len([u for u, _ in fetcher.calls if u == SEARCH_URL]) == 2


def test_block_on_first_request_fails_the_run() -> None:
    fetcher = FakeFetcher({"": [["1"]]}, fail={0: FetchError("HTTP 429", 429)})
    with pytest.raises(Blocked):
        run_fetch(fetcher)


def test_block_during_search_keeps_what_was_found() -> None:
    fetcher = FakeFetcher({"": [["1"], ["2"]]}, fail={10: FetchError("HTTP 999", 999)})
    pages = run_fetch(fetcher)
    assert len(pages) == 1  # the first search page; no detail pages after a block
    assert all(u == SEARCH_URL for u, _ in fetcher.calls)


def test_block_on_details_stops_details_only() -> None:
    fetcher = FakeFetcher({"": [["1", "2", "3"]]}, fail={"2": FetchError("HTTP 429", 429)})
    pages = run_fetch(fetcher)
    assert [p.url.rsplit("/", 1)[-1] for p in pages[1:]] == ["1"]


def test_missing_job_is_skipped() -> None:
    fetcher = FakeFetcher({"": [["1", "2"]]}, fail={"1": FetchError("HTTP 404", 404)})
    pages = run_fetch(fetcher)
    assert [p.url.rsplit("/", 1)[-1] for p in pages[1:]] == ["2"]


def test_redirect_to_authwall_counts_as_blocked() -> None:
    class AuthwallFetcher(FakeFetcher):
        def get(self, url, *, params=None):
            self.calls.append((url, params))
            return RawPage(url="https://www.linkedin.com/authwall?trk=x", body="<html></html>")

    with pytest.raises(Blocked):
        run_fetch(AuthwallFetcher({}))


def test_fetcher_does_not_retry_429() -> None:
    fetcher = LinkedinCrawler().create_fetcher()
    try:
        assert 429 not in fetcher.retry_status
        assert fetcher.delay == (3.0, 8.0)
    finally:
        fetcher.close()
