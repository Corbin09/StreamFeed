from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models import Category, CrawlRun, CrawlRunStatus, Post, Source, SourceStatus
from app.seed import load_seed_file, seed_sources

SEED_FILE = Path(__file__).resolve().parents[2] / "data/seeds/sources.yaml"


def make_source(session: Session, name: str = "test-source") -> Source:
    source = Source(type="itviec", name=name)
    session.add(source)
    session.flush()
    return source


def make_post(session: Session, source: Source, external_id: str, **fields) -> Post:
    post = Post(
        source_id=source.id,
        external_id=external_id,
        url=f"https://example.com/{external_id}",
        title=fields.pop("title", "Untitled"),
        **fields,
    )
    session.add(post)
    session.flush()
    return post


def search(session: Session, query: str) -> list[str]:
    tsquery = func.plainto_tsquery("simple", func.f_unaccent(query))
    stmt = (
        select(Post.title)
        .where(Post.search_vector.op("@@")(tsquery))
        .order_by(func.ts_rank(Post.search_vector, tsquery).desc())
    )
    return list(session.scalars(stmt))


def test_defaults(session: Session) -> None:
    source = make_source(session)
    post = make_post(session, source, "1", title="Backend Developer")
    session.refresh(source)
    session.refresh(post)
    assert source.status == SourceStatus.IDLE
    assert source.enabled is True
    assert post.category == Category.JOB
    assert post.attributes == {}
    assert post.is_hidden is False


def test_source_external_id_is_unique(session: Session) -> None:
    source = make_source(session)
    make_post(session, source, "dup")
    with pytest.raises(IntegrityError):
        make_post(session, source, "dup")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("lap trinh vien", "Tuyển Lập Trình Viên Python tại Đà Nẵng"),  # unaccented VI
        ("lập trình", "Tuyển Lập Trình Viên Python tại Đà Nẵng"),  # accented VI
        ("da nang", "Tuyển Lập Trình Viên Python tại Đà Nẵng"),  # đ -> d
        ("developer", "Senior React Developer"),  # EN
        ("fintech", "Senior React Developer"),  # matches company
    ],
)
def test_search_is_bilingual_and_accent_insensitive(
    session: Session, query: str, expected: str
) -> None:
    source = make_source(session)
    make_post(session, source, "vi", title="Tuyển Lập Trình Viên Python tại Đà Nẵng")
    make_post(session, source, "en", title="Senior React Developer", company="Acme Fintech")
    make_post(session, source, "other", title="Kế toán tổng hợp", body="Không liên quan")
    assert search(session, query) == [expected]


def test_title_match_ranks_above_body_match(session: Session) -> None:
    source = make_source(session)
    make_post(session, source, "a", title="Office Manager", body="We also use Python a bit")
    make_post(session, source, "b", title="Python Engineer")
    assert search(session, "python") == ["Python Engineer", "Office Manager"]


def test_trigram_similarity_on_unaccented_title(session: Session) -> None:
    source = make_source(session)
    make_post(session, source, "1", title="Kỹ sư phần mềm")
    score = session.scalar(
        text("SELECT similarity(f_unaccent(title), 'ky su phan mem') FROM posts")
    )
    assert score == pytest.approx(1.0)


def test_attributes_jsonb_containment(session: Session) -> None:
    source = make_source(session)
    make_post(
        session,
        source,
        "1",
        title="Go Developer",
        attributes={"level": "senior", "skills": ["go", "kubernetes"]},
        price_min=Decimal("2000"),
        price_max=Decimal("3000"),
        currency="USD",
    )
    make_post(session, source, "2", title="Intern", attributes={"level": "intern"})
    titles = session.scalars(
        select(Post.title).where(Post.attributes.contains({"skills": ["go"]}))
    ).all()
    assert titles == ["Go Developer"]


def test_status_check_constraints(session: Session) -> None:
    source = make_source(session)
    session.add(CrawlRun(source_id=source.id, status="bogus"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_crawl_run_defaults(session: Session) -> None:
    source = make_source(session)
    run = CrawlRun(source_id=source.id)
    session.add(run)
    session.flush()
    session.refresh(run)
    assert run.status == CrawlRunStatus.RUNNING
    assert run.started_at is not None


def test_seed_sources_is_idempotent_and_keeps_runtime_state(session: Session) -> None:
    entries = load_seed_file(SEED_FILE)
    assert {e["type"] for e in entries} == {"itviec", "facebook"}

    assert seed_sources(session, entries) == (2, 0)
    itviec = session.scalar(select(Source).where(Source.type == "itviec"))
    itviec.enabled = False  # simulate a runtime change from the admin UI
    session.flush()

    assert seed_sources(session, entries) == (0, 2)
    assert session.scalar(select(func.count()).select_from(Source)) == 2
    session.refresh(itviec)
    assert itviec.enabled is False


def test_migration_downgrade_and_upgrade(alembic_config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
