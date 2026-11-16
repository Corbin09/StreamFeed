import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.crawlers.base import BaseCrawler, NeedsLogin, RawPage, RawPost
from app.crawlers.raw_store import RawStore
from app.crawlers.registry import CrawlerRegistry
from app.crawlers.runner import LOCK_NAMESPACE, CrawlRunner, recover_stale_runs
from app.models import CrawlRun, CrawlRunStatus, CrawlRunTrigger, Post, Source, SourceStatus


class FakeFetcher:
    closed = 0

    def close(self) -> None:
        FakeFetcher.closed += 1


def page(*posts: tuple[str, str]) -> RawPage:
    """A JSON page listing (external_id, title) pairs."""
    body = json.dumps([{"id": pid, "title": title} for pid, title in posts], ensure_ascii=False)
    return RawPage(
        url=f"https://example.com/p{posts[0][0] if posts else 'empty'}",
        body=body,
        content_type="application/json",
    )


def make_crawler(
    pages: list[RawPage] | Callable[[], list[RawPage]], *, fail_parse_on: set[int] = frozenset()
) -> type[BaseCrawler]:
    class FakeCrawler(BaseCrawler):
        type = "fake"
        fetched_pages = 0

        def create_fetcher(self):
            return FakeFetcher()

        def fetch(self, ctx):
            for p in pages() if callable(pages) else pages:
                if ctx.should_stop:
                    return
                FakeCrawler.fetched_pages += 1
                yield p

        def parse(self, p: RawPage) -> list[RawPost]:
            items = json.loads(p.body)
            if any(item["id"] in fail_parse_on for item in items):
                raise ValueError("broken markup")
            return [
                RawPost(
                    external_id=str(i["id"]), url=f"https://example.com/{i['id']}", title=i["title"]
                )
                for i in items
            ]

    return FakeCrawler


@pytest.fixture
def make_runner(session_factory: sessionmaker[Session], tmp_path: Path):
    def _make(crawler: type[BaseCrawler]) -> CrawlRunner:
        registry = CrawlerRegistry()
        registry.register(crawler)
        return CrawlRunner(session_factory, RawStore(tmp_path), registry)

    return _make


@pytest.fixture
def source_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        source = Source(type="fake", name="Fake source", config={})
        session.add(source)
        session.commit()
        return source.id


def get(session_factory, model, ident):
    with session_factory() as session:
        return session.get(model, ident)


def titles(session_factory) -> list[str]:
    with session_factory() as session:
        return list(session.scalars(select(Post.title).order_by(Post.external_id)))


def test_successful_run(make_runner, session_factory, source_id, tmp_path) -> None:
    FakeFetcher.closed = 0
    runner = make_runner(
        make_crawler([page((1, "Lập trình viên Python"), (2, "Go Dev")), page((3, "QA"))])
    )

    result = runner.run(source_id)

    assert (result.status, result.pages, result.fetched, result.inserted, result.updated) == (
        "success",
        2,
        3,
        3,
        0,
    )
    assert titles(session_factory) == ["Lập trình viên Python", "Go Dev", "QA"]
    run = get(session_factory, CrawlRun, result.run_id)
    assert run.status == CrawlRunStatus.SUCCESS
    assert run.trigger == CrawlRunTrigger.SCHEDULE
    assert (run.fetched, run.inserted, run.finished_at is not None) == (3, 3, True)
    assert sorted(p.name for p in (tmp_path / run.raw_dir).iterdir()) == [
        "0001.json",
        "0002.json",
        "manifest.json",
    ]
    source = get(session_factory, Source, source_id)
    assert (source.status, source.consecutive_failures, source.last_run_at is not None) == (
        SourceStatus.OK,
        0,
        True,
    )
    assert FakeFetcher.closed == 1


def test_rerun_updates_and_keeps_user_flags(make_runner, session_factory, source_id) -> None:
    make_runner(make_crawler([page((1, "Old title"))])).run(source_id)
    with session_factory() as session:
        post = session.scalar(select(Post))
        post.is_saved, post.is_hidden = True, True
        first_seen = post.last_seen_at
        session.commit()

    result = make_runner(make_crawler([page((1, "New title"), (2, "Another"))])).run(source_id)

    assert (result.inserted, result.updated) == (1, 1)
    with session_factory() as session:
        post = session.scalar(select(Post).where(Post.external_id == "1"))
        assert post.title == "New title"
        assert (post.is_saved, post.is_hidden) == (True, True)
        assert post.last_seen_at > first_seen


def test_duplicate_external_ids_in_one_run(make_runner, session_factory, source_id) -> None:
    result = make_runner(make_crawler([page((1, "A")), page((1, "A again"))])).run(source_id)
    assert (result.status, result.fetched, result.inserted) == ("success", 1, 1)
    assert titles(session_factory) == ["A"]  # first non-empty value wins (merge_raw_posts)


def test_needs_login_stops_and_flags_source(make_runner, session_factory, source_id) -> None:
    def pages():
        yield page((1, "A"))
        raise NeedsLogin("redirected to /checkpoint")

    crawler = make_crawler(lambda: pages())
    result = make_runner(crawler).run(source_id)

    assert result.status == CrawlRunStatus.NEEDS_LOGIN
    assert "checkpoint" in result.error
    assert get(session_factory, Source, source_id).status == SourceStatus.NEEDS_LOGIN
    assert get(session_factory, CrawlRun, result.run_id).status == CrawlRunStatus.NEEDS_LOGIN
    assert titles(session_factory) == []  # nothing ingested from an interrupted run


def test_failures_count_up_and_mark_error_after_five(
    make_runner, session_factory, source_id
) -> None:
    def pages():
        raise RuntimeError("site down")
        yield  # pragma: no cover

    runner = make_runner(make_crawler(lambda: pages()))
    for expected in range(1, 6):
        result = runner.run(source_id)
        assert result.status == CrawlRunStatus.FAILED
        assert result.error == "RuntimeError: site down"
        source = get(session_factory, Source, source_id)
        assert source.consecutive_failures == expected
        assert source.status == (SourceStatus.ERROR if expected == 5 else SourceStatus.IDLE)

    make_runner(make_crawler([page((1, "A"))])).run(source_id)
    source = get(session_factory, Source, source_id)
    assert (source.status, source.consecutive_failures, source.last_error) == (
        SourceStatus.OK,
        0,
        None,
    )


def test_selector_drift_fails_the_run(make_runner, source_id) -> None:
    result = make_runner(make_crawler([page(), page()])).run(source_id)
    assert result.status == CrawlRunStatus.FAILED
    assert result.error == "selector drift: 0 posts parsed from 2 pages"


def test_parse_error_on_one_page_is_skipped(make_runner, session_factory, source_id) -> None:
    crawler = make_crawler([page((1, "A")), page((2, "Broken")), page((3, "C"))], fail_parse_on={2})
    result = make_runner(crawler).run(source_id)
    assert (result.status, result.parse_errors, result.fetched) == ("success", 1, 2)
    assert titles(session_factory) == ["A", "C"]


def test_max_posts_per_run_stops_fetching(make_runner, session_factory, source_id) -> None:
    with session_factory() as session:
        session.get(Source, source_id).config = {"max_posts_per_run": 3}
        session.commit()
    crawler = make_crawler([page((1, "A"), (2, "B")), page((3, "C"), (4, "D")), page((5, "E"))])

    result = make_runner(crawler).run(source_id)

    assert crawler.fetched_pages == 2
    assert result.fetched == 3
    assert titles(session_factory) == ["A", "B", "C"]


def test_claims_a_queued_run(make_runner, session_factory, source_id) -> None:
    with session_factory() as session:
        queued = CrawlRun(
            source_id=source_id, status=CrawlRunStatus.QUEUED, trigger=CrawlRunTrigger.MANUAL
        )
        session.add(queued)
        session.commit()

    result = make_runner(make_crawler([page((1, "A"))])).run(
        source_id, CrawlRunTrigger.MANUAL, run_id=queued.id
    )

    assert result.run_id == queued.id
    run = get(session_factory, CrawlRun, queued.id)
    assert (run.status, run.trigger) == (CrawlRunStatus.SUCCESS, CrawlRunTrigger.MANUAL)


def test_skips_when_source_is_locked(make_runner, session_factory, db_engine, source_id) -> None:
    with session_factory() as session:
        queued = CrawlRun(source_id=source_id, status=CrawlRunStatus.QUEUED)
        session.add(queued)
        session.commit()

    with db_engine.connect() as other_worker:
        other_worker.execute(
            text("SELECT pg_advisory_lock(:ns, :id)"), {"ns": LOCK_NAMESPACE, "id": source_id}
        )
        result = make_runner(make_crawler([page((1, "A"))])).run(source_id, run_id=queued.id)
        other_worker.execute(
            text("SELECT pg_advisory_unlock(:ns, :id)"), {"ns": LOCK_NAMESPACE, "id": source_id}
        )

    assert result.status == "skipped"
    assert get(session_factory, CrawlRun, queued.id) is None  # queued request dropped
    assert titles(session_factory) == []


def test_reparse_replays_saved_pages(make_runner, session_factory, source_id) -> None:
    result = make_runner(make_crawler([page((1, "A")), page((2, "B"))])).run(source_id)
    with session_factory() as session:
        session.execute(text("DELETE FROM posts"))
        session.commit()

    # The crawler would now fail if it touched the network; reparse must not fetch.
    offline = make_crawler(lambda: (_ for _ in ()).throw(AssertionError("network used")))
    replay = make_runner(offline).reparse(result.run_id)

    assert (replay.status, replay.pages, replay.inserted) == ("success", 2, 2)
    assert titles(session_factory) == ["A", "B"]


def test_unknown_source_type_raises(make_runner, session_factory) -> None:
    with session_factory() as session:
        source = Source(type="unknown", name="Unknown")
        session.add(source)
        session.commit()
    with pytest.raises(LookupError, match="No crawler registered"):
        make_runner(make_crawler([])).run(source.id)


def test_recover_stale_runs(session_factory, source_id) -> None:
    with session_factory() as session:
        session.add_all(
            [
                CrawlRun(source_id=source_id, status=CrawlRunStatus.RUNNING),
                CrawlRun(source_id=source_id, status=CrawlRunStatus.RUNNING),
            ]
        )
        session.flush()
        session.execute(
            text(
                "UPDATE crawl_runs SET started_at = now() - interval '2 hours' WHERE id = "
                "(SELECT min(id) FROM crawl_runs)"
            )
        )
        assert recover_stale_runs(session) == 1
        session.commit()
        statuses = sorted(session.scalars(select(CrawlRun.status)))
    assert statuses == ["failed", "running"]
