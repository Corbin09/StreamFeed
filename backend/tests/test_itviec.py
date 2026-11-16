from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.crawlers.base import CrawlContext, RawPage, SourceInfo
from app.crawlers.itviec import ItviecCrawler, parse_relative_time, search_paths
from app.crawlers.raw_store import RawStore
from app.crawlers.registry import CrawlerRegistry, load_builtin_crawlers
from app.crawlers.runner import CrawlRunner
from app.models import Post, Source

FIXTURES = Path(__file__).parent / "fixtures" / "itviec"
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LISTING = (FIXTURES / "listing.html").read_text(encoding="utf-8")
JAVA_SLUG = "middle-senior-back-end-developer-java-persol-career-tech-studio-vietnam-0150"
DETAILS = {
    "backend-developer-shinhan-ds-3750": "detail_vnd_up_to.html",
    JAVA_SLUG: "detail_usd_range.html",
    "assistant-manager-cloud-engineer-azure-english-fwd-vtc-5650": "detail_hidden_salary.html",
}


def fixture_page(name: str, url: str = "https://itviec.com/x") -> RawPage:
    return RawPage(url=url, body=(FIXTURES / name).read_text(encoding="utf-8"), fetched_at=NOW)


def test_builtin_registry_has_itviec() -> None:
    assert load_builtin_crawlers().get("itviec") is ItviecCrawler


def test_parse_listing_cards() -> None:
    posts = ItviecCrawler().parse(fixture_page("listing.html"))
    assert len(posts) == 6
    java = posts[2]
    assert (
        java.external_id
        == "middle-senior-back-end-developer-java-persol-career-tech-studio-vietnam-0150"
    )
    assert java.url == f"https://itviec.com/it-jobs/{java.external_id}"
    assert java.title == "Middle/Senior Back End Developer (Java)"
    assert java.company == "Persol Career Tech Studio Vietnam"
    assert (java.location_raw, java.work_mode_raw) == ("Ho Chi Minh", "Hybrid")
    assert java.tags == ["Java", "Spring", "JavaScript", "SQL"]
    assert java.posted_at == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)  # "1 hour ago"
    assert java.extra == {"expertise": "Backend Developer"}


def test_parse_detail_json_ld() -> None:
    [post] = ItviecCrawler().parse(fixture_page("detail_vnd_up_to.html"))
    assert post.external_id == "backend-developer-shinhan-ds-3750"
    assert post.company == "SHINHAN DS"
    assert (post.salary_raw, post.salary_currency_hint) == ("Up To 35M Gross", "USD")
    assert post.location_raw == "Hồ Chí Minh"
    assert "Core Banking" in post.body and "<p>" not in post.body
    assert "Benefits" in post.body
    assert post.extra["valid_through"] == "2026-12-21"


def test_parse_detail_with_text_experience_requirement() -> None:
    body = fixture_page("detail_usd_range.html").body.replace(
        '"experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":10}',
        '"experienceRequirements":"No requirements"',
    )
    assert '"No requirements"' in body
    [post] = ItviecCrawler().parse(RawPage(url="x", body=body))
    assert post.extra["experience_months"] == 0


def test_parse_unknown_page_returns_nothing() -> None:
    assert (
        ItviecCrawler().parse(RawPage(url="x", body="<html><body>Maintenance</body></html>")) == []
    )


@pytest.mark.parametrize(
    ("text", "expected_minutes"),
    [
        ("Posted 24 minutes ago", 24),
        ("an hour ago", 60),
        ("2 days ago", 2880),
        ("Đăng 3 ngày trước", 4320),
        ("Posted recently", None),
    ],
)
def test_relative_time(text: str, expected_minutes: int | None) -> None:
    result = parse_relative_time(text, NOW)
    if expected_minutes is None:
        assert result is None
    else:
        assert (NOW - result).total_seconds() == expected_minutes * 60


def test_search_paths() -> None:
    assert search_paths({}) == ["/it-jobs"]
    assert search_paths({"keywords": ["python", "react"], "locations": ["ha-noi"]}) == [
        "/it-jobs/python/ha-noi",
        "/it-jobs/react/ha-noi",
    ]


class FixtureFetcher:
    """Serves the listing fixture for page 1, an empty listing after that, and detail
    fixtures (or a minimal JSON-LD page) for job URLs."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def get(self, url: str, *, params=None) -> RawPage:
        self.requests.append(url + (f"?page={params['page']}" if params else ""))
        if "/it-jobs/" in url and url.rsplit("/", 1)[-1] not in ("it-jobs",):
            slug = url.rsplit("/", 1)[-1]
            if slug in DETAILS:
                return fixture_page(DETAILS[slug], url)
            return RawPage(url=url, body="<html></html>", fetched_at=NOW)
        if params and params.get("page", 1) > 1:
            return RawPage(url=url, body="<html><body>No more jobs</body></html>", fetched_at=NOW)
        return fixture_page("listing.html", url)

    def close(self) -> None:
        pass


def make_ctx(fetcher, **config) -> CrawlContext:
    return CrawlContext(
        run_id=1,
        source=SourceInfo(1, "itviec", "ITviec", config),
        fetcher=fetcher,
        max_posts=config.get("max_posts_per_run", 60),
    )


def test_fetch_stops_at_last_listing_page_then_fetches_details() -> None:
    fetcher = FixtureFetcher()
    pages = list(ItviecCrawler().fetch(make_ctx(fetcher, pages=3)))
    assert fetcher.requests[:2] == [
        "https://itviec.com/it-jobs",
        "https://itviec.com/it-jobs?page=2",
    ]
    assert len(pages) == 2 + 6  # two listing pages, six detail pages


def test_fetch_respects_max_posts_and_details_flag() -> None:
    fetcher = FixtureFetcher()
    pages = list(ItviecCrawler().fetch(make_ctx(fetcher, max_posts_per_run=2)))
    assert len(pages) == 1 + 2
    fetcher = FixtureFetcher()
    assert len(list(ItviecCrawler().fetch(make_ctx(fetcher, fetch_details=False)))) == 2


def test_end_to_end_run_with_fixtures(session_factory, tmp_path) -> None:
    class OfflineItviec(ItviecCrawler):
        def create_fetcher(self):
            return FixtureFetcher()

    registry = CrawlerRegistry()
    registry.register(OfflineItviec)
    with session_factory() as session:
        source = Source(type="itviec", name="ITviec test", config={"pages": 2})
        session.add(source)
        session.commit()

    result = CrawlRunner(session_factory, RawStore(tmp_path), registry).run(source.id)

    assert (result.status, result.fetched, result.inserted, result.parse_errors) == (
        "success",
        6,
        6,
        0,
    )
    with session_factory() as session:
        posts = {p.external_id: p for p in session.scalars(select(Post))}
    shinhan = posts["backend-developer-shinhan-ds-3750"]
    assert shinhan.title == "Backend Developer"
    assert (shinhan.price_max, shinhan.currency, shinhan.location) == (35_000_000, "VND", "hcmc")
    assert shinhan.posted_at == datetime(2026, 11, 16, 8, 22, tzinfo=UTC)  # from the card
    assert "Core Banking" in shinhan.body  # from the detail page
    assert shinhan.attributes["skills"] == ["c", "linux", "oracle", "javascript", "sql", "html"]
    java = posts["middle-senior-back-end-developer-java-persol-career-tech-studio-vietnam-0150"]
    assert java.title == "Middle/Senior Back End Developer (Java)"
    assert java.attributes["levels"] == ["mid", "senior"]
    assert (java.price_min, java.price_max, java.currency) == (1000, 1750, "USD")
    assert (
        posts["assistant-manager-cloud-engineer-azure-english-fwd-vtc-5650"].attributes[
            "salary_negotiable"
        ]
        is True
    )
    assert {p.language for p in posts.values()} == {"en"}
