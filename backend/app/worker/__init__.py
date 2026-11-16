"""The worker process: scheduler, queue poller, heartbeat and housekeeping.

See docs/ARCHITECTURE.md §4 (Worker) and §11 (Reliability). Run with `python -m app.worker`.

One worker runs one crawl at a time in its main loop:

1. Recover orphaned runs: `running` rows whose source advisory lock is free (the worker that
   owned them died).
2. Claim the oldest `queued` run ("Run now") with `FOR UPDATE SKIP LOCKED` and execute it.
3. Otherwise run the most overdue scheduled source. A source is due when
   `last_run_at + interval × backoff ± jitter` has passed; backoff doubles per consecutive
   failure, capped at ×8. Disabled sources, `needs_login` sources, sources without a plugin
   and sources with an active run are skipped.

The heartbeat and daily housekeeping run on an APScheduler background thread so they keep
going while a crawl is in progress.
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.crawlers.registry import CrawlerRegistry, UnknownSourceType, load_builtin_crawlers
from app.crawlers.runner import LOCK_NAMESPACE, CrawlRunner
from app.db import SessionLocal
from app.models import (
    CrawlRun,
    CrawlRunStatus,
    CrawlRunTrigger,
    Source,
    SourceStatus,
    WorkerHeartbeat,
)

logger = logging.getLogger(__name__)

MAX_BACKOFF_MULTIPLIER = 8
ACTIVE_STATUSES = (CrawlRunStatus.QUEUED, CrawlRunStatus.RUNNING)


def backoff_multiplier(consecutive_failures: int) -> int:
    return min(2 ** max(consecutive_failures, 0), MAX_BACKOFF_MULTIPLIER)


@dataclass
class TickResult:
    kind: str  # "queued" | "scheduled" | "idle"
    source_id: int | None = None
    run_id: int | None = None
    status: str | None = None


class Worker:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        runner: CrawlRunner | None = None,
        registry: CrawlerRegistry | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: random.Random | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.registry = registry or load_builtin_crawlers()
        self.runner = runner or CrawlRunner(session_factory, registry=self.registry)
        self.clock = clock
        self.rng = rng or random.Random()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        # Per-source jitter in minutes, drawn once per scheduling cycle.
        self._jitter: dict[int, float] = {}

    # ---------------------------------------------------------------- heartbeat
    def heartbeat(self) -> None:
        with self.session_factory() as session:
            stmt = insert(WorkerHeartbeat).values(worker_id=self.worker_id, beat_at=func.now())
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[WorkerHeartbeat.worker_id], set_={"beat_at": func.now()}
                )
            )
            session.commit()

    # ------------------------------------------------------------------ recovery
    def recover_orphans(self) -> int:
        """Fail `running` runs whose source lock is free: their worker is gone."""
        with self.session_factory() as session:
            running = session.execute(
                select(CrawlRun.id, CrawlRun.source_id).where(
                    CrawlRun.status == CrawlRunStatus.RUNNING
                )
            ).all()
        if not running:
            return 0

        engine = self.session_factory.kw["bind"]
        recovered = 0
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for run_id, source_id in running:
                if not conn.scalar(select(func.pg_try_advisory_lock(LOCK_NAMESPACE, source_id))):
                    continue  # a live crawl holds it
                try:
                    count = conn.execute(
                        text(
                            "UPDATE crawl_runs SET status = 'failed', finished_at = now(), "
                            "error = 'Worker stopped before the run finished' "
                            "WHERE id = :id AND status = 'running'"
                        ),
                        {"id": run_id},
                    ).rowcount
                    recovered += count or 0
                finally:
                    conn.execute(select(func.pg_advisory_unlock(LOCK_NAMESPACE, source_id)))
        if recovered:
            logger.warning("Marked %d orphaned run(s) as failed", recovered)
        return recovered

    # --------------------------------------------------------------------- queue
    def claim_queued(self) -> tuple[int, int] | None:
        """Atomically move the oldest queued run to `running`. Returns (run_id, source_id)."""
        with self.session_factory() as session:
            run = session.scalars(
                select(CrawlRun)
                .where(CrawlRun.status == CrawlRunStatus.QUEUED)
                .order_by(CrawlRun.started_at, CrawlRun.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if run is None:
                return None
            run.status = CrawlRunStatus.RUNNING
            session.commit()
            return run.id, run.source_id

    def _requeue(self, run_id: int) -> None:
        with self.session_factory() as session:
            run = session.get(CrawlRun, run_id)
            if run is not None and run.status == CrawlRunStatus.RUNNING:
                run.status = CrawlRunStatus.QUEUED
                session.commit()

    # ------------------------------------------------------------------ schedule
    def _jitter_for(self, source_id: int) -> float:
        if source_id not in self._jitter:
            j = self.settings.crawl_jitter_minutes
            self._jitter[source_id] = self.rng.uniform(-j, j) if j > 0 else 0.0
        return self._jitter[source_id]

    def next_due_at(self, source: Source) -> datetime | None:
        """When a source should run next; None means as soon as possible."""
        if source.last_run_at is None:
            return None
        minutes = source.interval_minutes * backoff_multiplier(source.consecutive_failures)
        minutes = max(1.0, minutes + self._jitter_for(source.id))
        return source.last_run_at + timedelta(minutes=minutes)

    def _has_plugin(self, source_type: str) -> bool:
        try:
            self.registry.get(source_type)
        except UnknownSourceType:
            return False
        return True

    def due_sources(self) -> list[int]:
        """Ids of sources whose scheduled run is due, most overdue first."""
        now = self.clock()
        with self.session_factory() as session:
            sources = session.scalars(
                select(Source).where(
                    Source.enabled.is_(True), Source.status != SourceStatus.NEEDS_LOGIN
                )
            ).all()
            active = set(
                session.scalars(
                    select(CrawlRun.source_id).where(CrawlRun.status.in_(ACTIVE_STATUSES))
                )
            )
        due: list[tuple[datetime, int]] = []
        for source in sources:
            if source.id in active or not self._has_plugin(source.type):
                continue
            at = self.next_due_at(source)
            if at is None or at <= now:
                due.append((at or datetime.min.replace(tzinfo=UTC), source.id))
        return [sid for _, sid in sorted(due)]

    # ---------------------------------------------------------------------- loop
    def tick(self) -> TickResult:
        """Do at most one crawl. Queued manual runs go before scheduled ones."""
        self.recover_orphans()

        claimed = self.claim_queued()
        if claimed is not None:
            run_id, source_id = claimed
            result = self.runner.run(source_id, CrawlRunTrigger.MANUAL, run_id=run_id)
            if result.status == "skipped":
                # The source was busy (e.g. a reparse); try again on a later tick.
                self._requeue(run_id)
            self._jitter.pop(source_id, None)
            return TickResult("queued", source_id, run_id, result.status)

        due = self.due_sources()
        if due:
            source_id = due[0]
            result = self.runner.run(source_id, CrawlRunTrigger.SCHEDULE)
            self._jitter.pop(source_id, None)
            return TickResult("scheduled", source_id, result.run_id, result.status)
        return TickResult("idle")

    def run_forever(self, stop: threading.Event) -> None:
        poll = self.settings.worker_poll_seconds
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            self._safe(self.heartbeat),
            "interval",
            seconds=30,
            next_run_time=datetime.now(UTC),
            id="heartbeat",
        )
        scheduler.add_job(
            self._safe(self.housekeeping),
            "interval",
            hours=24,
            next_run_time=datetime.now(UTC) + timedelta(minutes=1),
            id="housekeeping",
        )
        scheduler.start()
        logger.info("Worker %s started (poll every %ss)", self.worker_id, poll)
        try:
            while not stop.is_set():
                try:
                    result = self.tick()
                except Exception:  # noqa: BLE001 - keep the worker alive (DB restarts, ...)
                    logger.exception("Worker tick failed")
                    result = TickResult("idle")
                if result.kind == "idle":
                    stop.wait(poll)
        finally:
            scheduler.shutdown(wait=False)
            logger.info("Worker %s stopped", self.worker_id)

    @staticmethod
    def _safe(job: Callable[[], object]) -> Callable[[], None]:
        def wrapper() -> None:
            try:
                job()
            except Exception:  # noqa: BLE001
                logger.exception("Background job %s failed", job.__name__)

        wrapper.__name__ = job.__name__
        return wrapper

    # -------------------------------------------------------------- housekeeping
    def housekeeping(self) -> dict[str, int]:
        """Apply retention (ARCHITECTURE.md §15): raw pages and old crawl runs."""
        today = self.clock().date()
        removed_dirs = prune_raw_pages(
            Path(self.settings.data_dir) / "raw",
            today - timedelta(days=self.settings.raw_retention_days),
        )
        cutoff = self.clock() - timedelta(days=self.settings.run_retention_days)
        with self.session_factory() as session:
            deleted_runs = session.execute(
                delete(CrawlRun).where(
                    CrawlRun.started_at < cutoff, CrawlRun.status.not_in(ACTIVE_STATUSES)
                )
            ).rowcount
            stale_beats = session.execute(
                delete(WorkerHeartbeat).where(WorkerHeartbeat.beat_at < cutoff)
            ).rowcount
            session.commit()
        stats = {"raw_dirs": removed_dirs, "runs": deleted_runs or 0, "heartbeats": stale_beats}
        logger.info("Housekeeping: %s", stats)
        return stats


def prune_raw_pages(raw_root: Path, keep_from: date) -> int:
    """Delete `raw/<type>/<YYYY-MM-DD>/` folders dated before `keep_from`."""
    if not raw_root.is_dir():
        return 0
    removed = 0
    for type_dir in raw_root.iterdir():
        if not type_dir.is_dir():
            continue
        for day_dir in type_dir.iterdir():
            try:
                day = date.fromisoformat(day_dir.name)
            except ValueError:
                continue  # not a dated folder; leave it alone
            if day_dir.is_dir() and day < keep_from:
                shutil.rmtree(day_dir)
                removed += 1
    return removed
