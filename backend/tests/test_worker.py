"""Worker scheduling, queue claiming, orphan recovery, heartbeat and housekeeping."""

import random
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crawlers.raw_store import RawStore
from app.crawlers.registry import CrawlerRegistry
from app.crawlers.runner import LOCK_NAMESPACE, CrawlRunner
from app.models import CrawlRun, CrawlRunStatus, Post, Source, SourceStatus, WorkerHeartbeat
from app.worker import Worker, backoff_multiplier, prune_raw_pages
from tests.test_runner import make_crawler, page

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def registry() -> CrawlerRegistry:
    reg = CrawlerRegistry()
    reg.register(make_crawler([page((1, "Python Developer"), (2, "QA Engineer"))]))
    return reg


@pytest.fixture
def make_worker(session_factory: sessionmaker[Session], registry, tmp_path: Path):
    def _make(clock=lambda: NOW, jitter: int = 0, **settings) -> Worker:
        cfg = Settings(data_dir=tmp_path, crawl_jitter_minutes=jitter, **settings)
        runner = CrawlRunner(session_factory, RawStore(tmp_path), registry)
        return Worker(
            session_factory,
            runner=runner,
            registry=registry,
            settings=cfg,
            clock=clock,
            rng=random.Random(1),
            worker_id="test-worker",
        )

    return _make


_names = iter(range(1, 100_000))


def add_source(session_factory, type_: str = "fake", **kw) -> int:
    with session_factory() as session:
        source = Source(type=type_, name=kw.pop("name", f"{type_}-{next(_names)}"), **kw)
        session.add(source)
        session.commit()
        return source.id


def runs(session_factory) -> list[CrawlRun]:
    with session_factory() as session:
        return list(session.scalars(select(CrawlRun).order_by(CrawlRun.id)))


def test_backoff_multiplier() -> None:
    assert [backoff_multiplier(n) for n in range(6)] == [1, 2, 4, 8, 8, 8]


def test_due_sources_respects_interval_backoff_and_skips(make_worker, session_factory) -> None:
    never_ran = add_source(session_factory)
    overdue = add_source(session_factory, interval_minutes=60, last_run_at=NOW - timedelta(hours=2))
    not_yet = add_source(
        session_factory, interval_minutes=60, last_run_at=NOW - timedelta(minutes=30)
    )
    # 3 failures -> x8 = 480 minutes, so 2 hours ago is not due yet.
    backing_off = add_source(
        session_factory,
        interval_minutes=60,
        consecutive_failures=3,
        last_run_at=NOW - timedelta(hours=2),
    )
    add_source(session_factory, enabled=False)
    add_source(session_factory, status=SourceStatus.NEEDS_LOGIN)
    add_source(session_factory, type_="linkedin")  # no plugin installed
    busy = add_source(session_factory)
    with session_factory() as session:
        session.add(CrawlRun(source_id=busy, status=CrawlRunStatus.QUEUED))
        session.commit()

    due = make_worker().due_sources()
    assert due == [never_ran, overdue]
    assert not_yet not in due and backing_off not in due


def test_jitter_is_bounded_and_stable_until_the_source_runs(make_worker, session_factory) -> None:
    sid = add_source(session_factory, interval_minutes=120, last_run_at=NOW)
    worker = make_worker(jitter=15)
    with session_factory() as session:
        source = session.get(Source, sid)
    first = worker.next_due_at(source)
    assert first == worker.next_due_at(source)
    assert abs((first - NOW) - timedelta(minutes=120)) <= timedelta(minutes=15)


def test_tick_runs_scheduled_source(make_worker, session_factory) -> None:
    sid = add_source(session_factory)
    worker = make_worker()

    result = worker.tick()

    assert (result.kind, result.source_id, result.status) == ("scheduled", sid, "success")
    (run,) = runs(session_factory)
    assert (run.trigger, run.status, run.inserted) == ("schedule", "success", 2)
    # Just ran, so nothing is due any more.
    assert worker.tick().kind == "idle"


def test_queued_runs_go_first(make_worker, session_factory) -> None:
    add_source(session_factory, name="scheduled")
    manual = add_source(session_factory, name="manual", last_run_at=NOW)
    with session_factory() as session:
        queued = CrawlRun(source_id=manual, status=CrawlRunStatus.QUEUED, trigger="manual")
        session.add(queued)
        session.commit()

    result = make_worker().tick()

    assert (result.kind, result.run_id, result.status) == ("queued", queued.id, "success")
    run = next(r for r in runs(session_factory) if r.id == queued.id)
    assert (run.status, run.trigger, run.raw_dir is not None) == ("success", "manual", True)


def test_claim_is_atomic(make_worker, session_factory) -> None:
    sid = add_source(session_factory)
    with session_factory() as session:
        session.add(CrawlRun(source_id=sid, status=CrawlRunStatus.QUEUED))
        session.commit()
    worker = make_worker()
    assert worker.claim_queued() is not None
    assert worker.claim_queued() is None  # already moved to running


def test_queued_run_is_requeued_when_source_is_busy(make_worker, session_factory) -> None:
    sid = add_source(session_factory, last_run_at=NOW)
    with session_factory() as session:
        queued = CrawlRun(source_id=sid, status=CrawlRunStatus.QUEUED)
        session.add(queued)
        session.commit()

    engine = session_factory.kw["bind"]
    with engine.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(LOCK_NAMESPACE, sid)))
        result = make_worker().tick()
        holder.execute(select(func.pg_advisory_unlock(LOCK_NAMESPACE, sid)))

    assert result.status == "skipped"
    assert runs(session_factory)[0].status == CrawlRunStatus.QUEUED


def test_recover_orphans_only_touches_unlocked_sources(make_worker, session_factory) -> None:
    orphan_source = add_source(session_factory, name="orphan")
    live_source = add_source(session_factory, name="live")
    with session_factory() as session:
        session.add_all(
            [
                CrawlRun(source_id=orphan_source, status=CrawlRunStatus.RUNNING),
                CrawlRun(source_id=live_source, status=CrawlRunStatus.RUNNING),
            ]
        )
        session.commit()

    engine = session_factory.kw["bind"]
    with engine.connect() as holder:
        holder.execute(select(func.pg_advisory_lock(LOCK_NAMESPACE, live_source)))
        recovered = make_worker().recover_orphans()
        holder.execute(select(func.pg_advisory_unlock(LOCK_NAMESPACE, live_source)))

    assert recovered == 1
    statuses = {r.source_id: (r.status, r.error) for r in runs(session_factory)}
    assert statuses[orphan_source] == ("failed", "Worker stopped before the run finished")
    assert statuses[live_source] == ("running", None)


def test_heartbeat_upserts(make_worker, session_factory) -> None:
    worker = make_worker()
    worker.heartbeat()
    worker.heartbeat()
    with session_factory() as session:
        beats = session.scalars(select(WorkerHeartbeat)).all()
    assert [b.worker_id for b in beats] == ["test-worker"]


def test_housekeeping_prunes_raw_pages_and_old_runs(make_worker, session_factory, tmp_path) -> None:
    sid = add_source(session_factory)
    old_dir = tmp_path / "raw" / "fake" / (NOW.date() - timedelta(days=40)).isoformat() / "1"
    new_dir = tmp_path / "raw" / "fake" / NOW.date().isoformat() / "2"
    for d in (old_dir, new_dir):
        d.mkdir(parents=True)
        (d / "0001.html").write_text("x")
    with session_factory() as session:
        session.add_all(
            [
                CrawlRun(
                    source_id=sid,
                    status=CrawlRunStatus.SUCCESS,
                    started_at=NOW - timedelta(days=100),
                ),
                CrawlRun(source_id=sid, status=CrawlRunStatus.SUCCESS, started_at=NOW),
            ]
        )
        session.commit()

    stats = make_worker().housekeeping()

    assert stats["raw_dirs"] == 1 and stats["runs"] == 1
    assert not old_dir.exists() and new_dir.exists()
    assert len(runs(session_factory)) == 1


def test_prune_ignores_unexpected_folders(tmp_path) -> None:
    (tmp_path / "raw" / "fake" / "notes").mkdir(parents=True)
    assert prune_raw_pages(tmp_path / "raw", NOW.date()) == 0
    assert prune_raw_pages(tmp_path / "missing", NOW.date()) == 0


def test_run_forever_stops_on_event(make_worker, session_factory) -> None:
    add_source(session_factory)
    worker = make_worker(worker_poll_seconds=1)
    stop = threading.Event()
    thread = threading.Thread(target=worker.run_forever, args=(stop,))
    thread.start()
    deadline = datetime.now(UTC) + timedelta(seconds=10)
    while datetime.now(UTC) < deadline:
        with session_factory() as session:
            if session.scalar(select(func.count()).select_from(Post)) == 2:
                break
        stop.wait(0.1)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Post)) == 2
        assert session.scalar(select(func.count()).select_from(WorkerHeartbeat)) == 1


def test_daily_window_source_runs_once_a_day(make_worker, session_factory) -> None:
    from zoneinfo import ZoneInfo

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    yesterday = datetime(2026, 11, 16, 7, 30, tzinfo=vn)
    sid = add_source(
        session_factory,
        interval_minutes=1440,
        last_run_at=yesterday,
        config={"daily_window": "07:00-09:00"},
    )
    before_window = make_worker(clock=lambda: datetime(2026, 11, 17, 6, 0, tzinfo=vn))
    assert before_window.due_sources() == []
    after_window = make_worker(clock=lambda: datetime(2026, 11, 17, 9, 0, tzinfo=vn))
    assert after_window.due_sources() == [sid]
    with session_factory() as session:
        session.get(Source, sid).last_run_at = datetime(2026, 11, 17, 8, 0, tzinfo=vn)
        session.commit()
    assert after_window.due_sources() == []  # already ran today


def test_invalid_daily_window_falls_back_to_interval(make_worker, session_factory) -> None:
    sid = add_source(session_factory, config={"daily_window": "soon"})
    assert make_worker().due_sources() == [sid]  # never ran -> due
