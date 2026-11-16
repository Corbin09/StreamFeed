from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.crawlers.base import RawPost
from app.models import Post, Source
from app.pipeline.dedup import content_hash
from app.pipeline.ingest import build_row, merge_raw_posts, process_posts

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def make_source(session: Session, name: str) -> int:
    source = Source(type=name, name=name)
    session.add(source)
    session.flush()
    return source.id


def raw(external_id: str, title: str = "Backend Developer", **kwargs) -> RawPost:
    return RawPost(external_id=external_id, url=f"https://x/{external_id}", title=title, **kwargs)


def test_merge_fills_gaps_and_keeps_first_values() -> None:
    card = raw(
        "1",
        "Senior Dev (Java)",
        company="Acme",
        work_mode_raw="Hybrid",
        tags=["Java"],
        posted_at=T0,
        extra={"expertise": "Backend"},
    )
    detail = raw(
        "1",
        "Senior Dev Java",
        body="Full description",
        salary_raw="1,000 USD",
        posted_at=T0 - timedelta(hours=8),
        extra={"expertise": None, "employment_type": "FT"},
    )
    other = raw("2", "Other")

    merged = merge_raw_posts([card, other, detail])

    assert [p.external_id for p in merged] == ["1", "2"]
    first = merged[0]
    assert (first.title, first.body, first.salary_raw, first.posted_at) == (
        "Senior Dev (Java)",
        "Full description",
        "1,000 USD",
        T0,
    )
    assert first.extra == {"expertise": "Backend", "employment_type": "FT"}
    assert card.extra == {"expertise": "Backend"}  # inputs are not mutated


def test_build_row_normalizes_a_vietnamese_post() -> None:
    post = raw(
        "fb-1",
        "Tuyển Senior ReactJS Developer",
        body="Công ty mình cần tuyển gấp dev ReactJS, NodeJS. Làm việc tại Cầu Giấy, Hà Nội.",
        location_raw="Hà Nội",
        salary_raw="25 - 35 triệu",
        work_mode_raw="Làm việc tại văn phòng",
    )
    row = build_row(1, "job", post)
    assert row["language"] == "vi"
    assert row["location"] == "hanoi"
    assert (row["price_min"], row["price_max"], row["currency"]) == (
        Decimal(25_000_000),
        Decimal(35_000_000),
        "VND",
    )
    assert row["attributes"] == {
        "levels": ["senior"],
        "skills": ["react", "nodejs"],  # free text: no structured tags
        "locations": ["hanoi"],
        "work_mode": "onsite",
        "salary_text": "25 - 35 triệu",
    }


def test_remote_work_mode_adds_remote_location() -> None:
    row = build_row(1, "job", raw("1", location_raw="Đà Nẵng", work_mode_raw="Remote"))
    assert row["attributes"]["locations"] == ["danang", "remote"]
    assert row["location"] == "danang"


def test_negotiable_salary() -> None:
    row = build_row(1, "job", raw("1", salary_raw="Thỏa thuận"))
    assert row["price_min"] is None and row["attributes"]["salary_negotiable"] is True


def test_upsert_then_update(session: Session) -> None:
    sid = make_source(session, "itviec")
    first = process_posts(session, sid, "job", [raw("1", body="v1"), raw("2", "QA")])
    second = process_posts(session, sid, "job", [raw("1", body="v2 longer body text")])
    assert (first.inserted, first.updated, second.inserted, second.updated) == (2, 0, 0, 1)
    assert session.scalar(select(Post.body).where(Post.external_id == "1")) == "v2 longer body text"


def test_invalid_attributes_are_skipped(session: Session) -> None:
    sid = make_source(session, "itviec")
    result = process_posts(
        session,
        sid,
        "job",
        [
            raw("bad", extra={"experience_months": "lots"}),
            raw("ok", extra={"experience_months": 12}),
        ],
    )
    assert (result.inserted, result.skipped) == (1, 1)


def test_non_hiring_posts_filtered_for_mixed_sources(session: Session) -> None:
    sid = make_source(session, "facebook")
    posts = [raw("1", "Cần tuyển dev PHP gấp"), raw("2", "Có ai đi cafe cuối tuần không?")]
    result = process_posts(session, sid, "job", posts, filter_non_jobs=True)
    assert (result.inserted, result.skipped) == (1, 1)


def test_cross_source_duplicates_link_to_the_oldest(session: Session) -> None:
    itviec = make_source(session, "itviec")
    linkedin = make_source(session, "linkedin")
    process_posts(session, itviec, "job", [raw("a", "Backend Developer", company="Shinhan DS")])
    session.execute(text("UPDATE posts SET crawled_at = now() - interval '1 day'"))
    # Same job on LinkedIn, different spacing/case/accents in company name
    process_posts(session, linkedin, "job", [raw("b", "Backend  developer", company="SHINHAN DS")])
    process_posts(session, linkedin, "job", [raw("c", "Frontend Developer", company="Shinhan DS")])

    rows = {p.external_id: p for p in session.scalars(select(Post))}
    assert rows["b"].duplicate_of_id == rows["a"].id
    assert rows["a"].duplicate_of_id is None
    assert rows["c"].duplicate_of_id is None


def test_same_source_reposts_are_not_linked(session: Session) -> None:
    sid = make_source(session, "itviec")
    process_posts(session, sid, "job", [raw("a", company="Acme")])
    process_posts(session, sid, "job", [raw("b", company="Acme")])
    assert session.scalars(select(Post.duplicate_of_id)).all() == [None, None]


def test_content_hash_falls_back_to_body_without_company() -> None:
    a = content_hash("Tuyển dev", None, "Cần tuyển 2 dev PHP, lương 15tr")
    b = content_hash("Tuyển DEV!!", None, "Cần tuyển 2 dev PHP,  lương 15tr")
    c = content_hash("Tuyển dev", None, "Cần tuyển 3 dev Java")
    assert a == b != c
