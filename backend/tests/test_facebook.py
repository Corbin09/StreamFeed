"""Facebook groups crawler: parsing of the saved JSON, block detection, time labels.

`fixtures/facebook/group_sample.json` is synthetic (same format as a saved run). The
browser-driven fetch is exercised in live runs; here only its guard rails are tested.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.crawlers.base import CrawlContext, NeedsLogin, RawPage, SourceInfo
from app.crawlers.facebook import (
    FacebookCrawler,
    group_slug,
    group_url,
    is_blocked,
    parse_post_time,
)
from app.pipeline.classify import is_hiring_post
from app.pipeline.ingest import build_row

FIXTURE = Path(__file__).parent / "fixtures" / "facebook" / "group_sample.json"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
GROUP = "123456789"


def sample_page() -> RawPage:
    return RawPage(
        url=group_url(GROUP),
        body=FIXTURE.read_text(encoding="utf-8"),
        content_type="application/json",
        fetched_at=NOW,
    )


@pytest.mark.parametrize(
    ("value", "slug"),
    [
        ("https://www.facebook.com/groups/123456789/?ref=share", "123456789"),
        ("https://m.facebook.com/groups/vieclamit", "vieclamit"),
        ("123456789", "123456789"),
        (" vieclamit ", "vieclamit"),
    ],
)
def test_group_slug(value, slug) -> None:
    assert group_slug(value) == slug


def test_group_slug_rejects_other_urls() -> None:
    with pytest.raises(ValueError):
        group_slug("https://www.facebook.com/somepage/")


@pytest.mark.parametrize(
    ("url", "text", "blocked"),
    [
        ("https://www.facebook.com/groups/1/", "Feed", False),
        ("https://www.facebook.com/login/?next=x", "", True),
        ("https://www.facebook.com/checkpoint/828281030927956/", "", True),
        ("https://www.facebook.com/two_step_verification/authentication/", "", True),
        ("https://www.facebook.com/groups/1/", "You’re Temporarily Blocked", True),
        ("https://www.facebook.com/groups/1/", "Bạn tạm thời bị chặn", True),
    ],
)
def test_is_blocked(url, text, blocked) -> None:
    assert (is_blocked(url, text) is not None) is blocked


@pytest.mark.parametrize(
    ("label", "delta"),
    [
        ("2h", timedelta(hours=2)),
        ("15m", timedelta(minutes=15)),
        ("3d", timedelta(days=3)),
        ("5 giờ", timedelta(hours=5)),
        ("Just now", timedelta(0)),
        ("Vừa xong", timedelta(0)),
        ("Yesterday at 9:15 PM", timedelta(days=1)),
        ("2 hours ago", timedelta(hours=2)),
    ],
)
def test_parse_post_time(label, delta) -> None:
    assert parse_post_time(label, NOW) == NOW - delta


def test_parse_post_time_unknown() -> None:
    assert parse_post_time("November 3 at 10:00", NOW) is None
    assert parse_post_time("", NOW) is None


def test_parse_saved_group_page() -> None:
    posts = FacebookCrawler().parse(sample_page())
    assert len(posts) == 4  # the photo-only post has no text
    first, second, question, no_link = posts

    assert first.external_id == f"{GROUP}:987654321"
    assert first.url == f"https://www.facebook.com/groups/{GROUP}/posts/987654321/"
    assert first.title == "[HN] Tuyển Senior Python Developer"
    assert first.author == "Nguyen Van A"
    assert first.salary_raw == "Lương: 25-35 triệu"
    assert first.work_mode_raw.startswith("Hybrid")
    assert first.posted_at == NOW - timedelta(hours=2)

    assert second.external_id == f"{GROUP}:111222333"
    assert second.posted_at == NOW - timedelta(hours=5)  # from the aria-label
    assert second.salary_raw == "Salary up to $2,000"

    assert question.posted_at == NOW
    assert no_link.external_id.startswith(f"{GROUP}:h")
    assert no_link.url == group_url(GROUP)


def test_pipeline_keeps_hiring_posts_and_normalizes_them() -> None:
    crawler = FacebookCrawler()
    assert crawler.filter_non_jobs is True
    posts = crawler.parse(sample_page())
    hiring = [p for p in posts if is_hiring_post(f"{p.title}\n{p.body}")]
    assert [p.external_id.split(":")[1] for p in hiring][:2] == ["987654321", "111222333"]
    assert all("hỏi học Java" not in p.body for p in hiring)  # the question is dropped

    row = build_row(1, "job", posts[0])
    assert row["location"] == "hanoi"
    assert (row["price_min"], row["price_max"], row["currency"]) == (25_000_000, 35_000_000, "VND")
    assert row["attributes"]["levels"] == ["senior"]
    assert row["attributes"]["work_mode"] == "hybrid"
    assert "python" in row["attributes"]["skills"]

    remote = build_row(1, "job", posts[1])
    assert remote["attributes"]["work_mode"] == "remote"
    assert (remote["price_max"], remote["currency"]) == (2000, "USD")


def test_fetch_without_session_needs_login(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(FacebookCrawler, "session_path", staticmethod(lambda: tmp_path / "x.json"))
    ctx = CrawlContext(
        run_id=1,
        source=SourceInfo(1, "facebook", "FB", {"groups": [GROUP]}),
        fetcher=None,
        max_posts=10,
    )
    with pytest.raises(NeedsLogin):
        list(FacebookCrawler().fetch(ctx))


def test_fetch_without_groups_fails(monkeypatch, tmp_path) -> None:
    session = tmp_path / "facebook.json"
    session.write_text("{}")
    monkeypatch.setattr(FacebookCrawler, "session_path", staticmethod(lambda: session))
    ctx = CrawlContext(
        run_id=1, source=SourceInfo(1, "facebook", "FB", {}), fetcher=None, max_posts=10
    )
    with pytest.raises(ValueError, match="No groups"):
        list(FacebookCrawler().fetch(ctx))


def test_fetcher_uses_the_saved_session_and_refreshes_it() -> None:
    fetcher = FacebookCrawler().create_fetcher()
    try:
        assert fetcher.storage_state == FacebookCrawler.session_path()
        assert fetcher.save_state is True
        assert fetcher.delay == (3.0, 8.0)
    finally:
        fetcher.close()


def test_login_complete_detection() -> None:
    from app.crawlers.login import SITES, login_complete

    site = SITES["facebook"]
    assert login_complete(site, {"c_user", "xs"}, "https://www.facebook.com/")
    assert not login_complete(site, {"datr"}, "https://www.facebook.com/")
    assert not login_complete(site, {"c_user"}, "https://www.facebook.com/checkpoint/123/")
    assert not login_complete(site, {"c_user"}, "https://www.facebook.com/two_step_verification/x")
