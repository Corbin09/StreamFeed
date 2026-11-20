import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.crawlers.base import RawPage
from app.crawlers.linkedin import (
    LinkedinCrawler,
    clean_description,
    is_blocked,
    normalize_salary,
    search_url,
)
from app.pipeline.ingest import build_row

FIXTURE = Path(__file__).parent / "fixtures/linkedin/search_sample.json"
NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)


def sample_page() -> RawPage:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return RawPage(
        url=data["url"],
        body=FIXTURE.read_text(encoding="utf-8"),
        content_type="application/json",
        fetched_at=NOW,
    )


def test_parse_jobs_from_panes_and_cards() -> None:
    posts = LinkedinCrawler().parse(sample_page())
    assert [p.external_id for p in posts] == ["4400000001", "4400000002", "4400000003"]

    first = posts[0]
    assert first.url == "https://www.linkedin.com/jobs/view/4400000001/"
    assert (first.title, first.company) == ("Senior Python Engineer", "Example Tech")
    assert first.location_raw == "Hanoi, Hanoi, Vietnam"
    assert first.work_mode_raw == "hybrid"
    assert first.salary_raw == "2000 - 3000 USD"
    assert first.posted_at == NOW - timedelta(hours=3)
    assert first.extra == {"levels": ["mid", "senior"], "employment_type": "FULL_TIME"}
    assert first.body.startswith("We build data tools")
    assert "Show less" not in first.body and "Medical insurance" not in first.body

    second = posts[1]
    assert second.body == "Tuyển lập trình viên Java Spring Boot, 2 năm kinh nghiệm."
    assert second.work_mode_raw == "on-site"
    assert second.extra["employment_type"] == "CONTRACTOR"
    assert second.posted_at == NOW - timedelta(days=1)

    # The pane did not load: the card still gives title, company, location and mode.
    card_only = posts[2]
    assert (card_only.title, card_only.company) == ("Remote QA Engineer", "Sample QA Co")
    assert card_only.location_raw == "Vietnam (Remote)"
    assert card_only.work_mode_raw == "remote"
    assert card_only.body == "" and card_only.posted_at is None


def test_rows_are_normalized() -> None:
    posts = LinkedinCrawler().parse(sample_page())
    row = build_row(1, "job", posts[0])
    assert row["location"] == "hanoi"
    assert (row["price_min"], row["price_max"], row["currency"]) == (2000, 3000, "USD")
    assert row["attributes"]["levels"] == ["senior"]  # from the title, before the fallback
    assert row["attributes"]["work_mode"] == "hybrid"
    assert "python" in row["attributes"]["skills"]
    assert build_row(1, "job", posts[1])["location"] == "hcmc"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,000.00 - $2,000.00", "1000 - 2000 USD"),
        ("$60K/yr - $90K/yr", "5000 - 7500 USD"),
        ("VND 20,000,000.00/mo", "20000000 VND"),
        (None, None),
    ],
)
def test_normalize_salary(text, expected) -> None:
    assert normalize_salary(text) == expected


def test_block_detection_and_urls() -> None:
    assert is_blocked("https://www.linkedin.com/checkpoint/challenge/abc") is not None
    assert is_blocked("https://www.linkedin.com/authwall?trk=x") is not None
    assert is_blocked("https://www.linkedin.com/jobs/search-results/?x=1") is None
    url = search_url({"keywords": "java developer", "location": "Vietnam"})
    assert "keywords=java+developer" in url and "f_TPR=r86400" in url and "sortBy=DD" in url


def test_clean_description() -> None:
    assert clean_description("About the job\n\nHello\n\n… more") == "Hello"


def test_page_scripts_are_valid_javascript(tmp_path) -> None:
    import shutil
    import subprocess

    from app.crawlers import linkedin

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    for name in ("_CARDS_JS", "_PANE_ID_JS", "_EXPAND_JS", "_PANE_JS"):
        path = tmp_path / f"{name}.js"
        path.write_text(f"const f = {getattr(linkedin, name).strip()};\n", encoding="utf-8")
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_registered_with_login_session() -> None:
    from app.crawlers.registry import load_builtin_crawlers

    crawler = load_builtin_crawlers().get("linkedin")
    assert crawler.min_interval_minutes == 1440
    assert crawler.filter_non_jobs is False
