"""Executes one crawl run for one source. See docs/ARCHITECTURE.md, section 4 "CrawlRunner"."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.crawlers.base import BaseCrawler, CrawlContext, NeedsLogin, RawPage, RawPost, SourceInfo
from app.crawlers.raw_store import RawStore
from app.crawlers.registry import CrawlerRegistry, load_builtin_crawlers
from app.db import SessionLocal
from app.models import CrawlRun, CrawlRunStatus, CrawlRunTrigger, Source, SourceStatus
from app.pipeline.ingest import IngestResult, merge_raw_posts, process_posts

logger = logging.getLogger(__name__)

# Namespace for pg advisory locks: (LOCK_NAMESPACE, source_id) = "a crawl of this source".
LOCK_NAMESPACE = 7301
INGEST_BATCH_SIZE = 50
ERROR_AFTER_FAILURES = 5
MAX_ERROR_LENGTH = 2000


@dataclass
class RunResult:
    run_id: int | None
    status: str
    pages: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    parse_errors: int = 0
    skipped: int = 0  # dropped by the pipeline (not a hiring post, invalid attributes)
    error: str | None = None


class CrawlRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        raw_store: RawStore | None = None,
        registry: CrawlerRegistry | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.raw_store = raw_store or RawStore(Path(get_settings().data_dir))
        self.registry = registry or load_builtin_crawlers()

    # ------------------------------------------------------------------ locking
    def _try_lock(self, source_id: int) -> Connection | None:
        """Session-level advisory lock held on a dedicated autocommit connection."""
        engine = self.session_factory.kw["bind"]
        conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired = conn.scalar(select(func.pg_try_advisory_lock(LOCK_NAMESPACE, source_id)))
        if acquired:
            return conn
        conn.close()
        return None

    @staticmethod
    def _unlock(conn: Connection, source_id: int) -> None:
        try:
            conn.execute(select(func.pg_advisory_unlock(LOCK_NAMESPACE, source_id)))
        finally:
            conn.close()

    # ---------------------------------------------------------------------- run
    def run(
        self,
        source_id: int,
        trigger: CrawlRunTrigger = CrawlRunTrigger.SCHEDULE,
        run_id: int | None = None,
    ) -> RunResult:
        """Run one crawl. `run_id` claims an existing queued row (manual "Run now")."""
        lock = self._try_lock(source_id)
        if lock is None:
            logger.info("Source %s is already being crawled; skipping", source_id)
            if run_id is not None:
                self._drop_queued(run_id)
            return RunResult(run_id=None, status="skipped", error="another run is in progress")
        try:
            return self._run_locked(source_id, trigger, run_id)
        finally:
            self._unlock(lock, source_id)

    def _drop_queued(self, run_id: int) -> None:
        with self.session_factory() as session:
            run = session.get(CrawlRun, run_id)
            if run is not None and run.status == CrawlRunStatus.QUEUED:
                session.delete(run)
                session.commit()

    def _run_locked(
        self, source_id: int, trigger: CrawlRunTrigger, run_id: int | None
    ) -> RunResult:
        with self.session_factory() as session:
            source = session.get(Source, source_id)
            if source is None:
                raise LookupError(f"Source {source_id} not found")
            crawler = self.registry.get(source.type)()
            info = SourceInfo(source.id, source.type, source.name, dict(source.config))

            run = session.get(CrawlRun, run_id) if run_id is not None else None
            if run is None:
                run = CrawlRun(source_id=source.id, trigger=trigger)
                session.add(run)
            run.status = CrawlRunStatus.RUNNING
            run.started_at = func.now()
            session.flush()
            writer = self.raw_store.open_run(source.type, run.id)
            run.raw_dir = writer.relative_dir
            session.commit()
            result = RunResult(run_id=run.id, status=CrawlRunStatus.RUNNING)

        log = logging.LoggerAdapter(logger, {"source": info.name, "run_id": result.run_id})
        log.info("Crawl started (%s, trigger=%s)", info.type, trigger)

        try:
            posts = self._fetch_and_parse(crawler, info, result, writer)
            self._ingest(info, crawler, posts, result)
            if result.pages > 0 and result.fetched == 0:
                result.status = CrawlRunStatus.FAILED
                result.error = f"selector drift: 0 posts parsed from {result.pages} pages"
            else:
                result.status = CrawlRunStatus.SUCCESS
        except NeedsLogin as exc:
            result.status = CrawlRunStatus.NEEDS_LOGIN
            result.error = str(exc) or "login required"
        except Exception as exc:  # noqa: BLE001 - any crawler failure must be recorded
            log.exception("Crawl failed")
            result.status = CrawlRunStatus.FAILED
            result.error = f"{type(exc).__name__}: {exc}"

        self._finish(source_id, result)
        log.info(
            "Crawl finished: %s (pages=%d posts=%d inserted=%d updated=%d skipped=%d "
            "parse_errors=%d)%s",
            result.status,
            result.pages,
            result.fetched,
            result.inserted,
            result.updated,
            result.skipped,
            result.parse_errors,
            f" error={result.error}" if result.error else "",
        )
        return result

    def _fetch_and_parse(
        self, crawler: BaseCrawler, info: SourceInfo, result: RunResult, writer
    ) -> list[RawPost]:
        max_posts = int(info.config.get("max_posts_per_run", crawler.default_max_posts))
        fetcher = crawler.create_fetcher()
        posts: list[RawPost] = []
        seen_ids: set[str] = set()
        try:
            ctx = CrawlContext(
                run_id=result.run_id, source=info, fetcher=fetcher, max_posts=max_posts
            )
            for page in crawler.fetch(ctx):
                writer.save(page)
                result.pages += 1
                parsed = self._parse_page(crawler, page, result)
                posts.extend(parsed)
                seen_ids.update(p.external_id for p in parsed)
                ctx.posts_seen = len(seen_ids)
        finally:
            fetcher.close()
        merged = merge_raw_posts(posts)[:max_posts]
        result.fetched = len(merged)
        return merged

    @staticmethod
    def _parse_page(crawler: BaseCrawler, page: RawPage, result: RunResult) -> list[RawPost]:
        try:
            return crawler.parse(page)
        except Exception:  # noqa: BLE001 - one bad page must not fail the run
            result.parse_errors += 1
            logger.exception("Failed to parse %s", page.url)
            return []

    def _ingest(
        self, info: SourceInfo, crawler: BaseCrawler, posts: list[RawPost], result: RunResult
    ) -> None:
        total = IngestResult()
        with self.session_factory() as session:
            for start in range(0, len(posts), INGEST_BATCH_SIZE):
                batch = posts[start : start + INGEST_BATCH_SIZE]
                total += process_posts(
                    session,
                    info.id,
                    crawler.category,
                    batch,
                    filter_non_jobs=crawler.filter_non_jobs,
                )
                session.commit()
        result.inserted, result.updated, result.skipped = (
            total.inserted,
            total.updated,
            total.skipped,
        )

    def _finish(self, source_id: int, result: RunResult) -> None:
        with self.session_factory() as session:
            run = session.get(CrawlRun, result.run_id)
            run.status = result.status
            run.finished_at = func.now()
            run.fetched, run.inserted, run.updated = result.fetched, result.inserted, result.updated
            run.error = result.error[:MAX_ERROR_LENGTH] if result.error else None

            source = session.get(Source, source_id)
            source.last_run_at = func.now()
            if result.status == CrawlRunStatus.SUCCESS:
                source.status = SourceStatus.OK
                source.consecutive_failures = 0
                source.last_error = None
            elif result.status == CrawlRunStatus.NEEDS_LOGIN:
                source.status = SourceStatus.NEEDS_LOGIN
                source.last_error = run.error
            else:
                source.consecutive_failures += 1
                source.last_error = run.error
                if source.consecutive_failures >= ERROR_AFTER_FAILURES:
                    source.status = SourceStatus.ERROR
            session.commit()

    # ------------------------------------------------------------------ reparse
    def reparse(self, run_id: int) -> RunResult:
        """Re-parse a finished run's saved pages and upsert the posts. No network access."""
        with self.session_factory() as session:
            run = session.get(CrawlRun, run_id)
            if run is None:
                raise LookupError(f"Crawl run {run_id} not found")
            if not run.raw_dir:
                raise LookupError(f"Crawl run {run_id} has no saved raw pages")
            source = session.get(Source, run.source_id)
            crawler = self.registry.get(source.type)()
            info = SourceInfo(source.id, source.type, source.name, dict(source.config))
            raw_dir = run.raw_dir

        lock = self._try_lock(info.id)
        if lock is None:
            return RunResult(run_id=run_id, status="skipped", error="another run is in progress")
        try:
            result = RunResult(run_id=run_id, status=CrawlRunStatus.SUCCESS)
            posts: list[RawPost] = []
            for page in self.raw_store.load_run(raw_dir):
                result.pages += 1
                posts.extend(self._parse_page(crawler, page, result))
            posts = merge_raw_posts(posts)
            result.fetched = len(posts)
            self._ingest(info, crawler, posts, result)
            return result
        finally:
            self._unlock(lock, info.id)


def recover_stale_runs(session: Session, older_than_minutes: int = 60) -> int:
    """Mark runs stuck in 'running' (e.g. after a worker crash) as failed. Returns the count."""
    result = session.execute(
        text(
            "UPDATE crawl_runs SET status = 'failed', finished_at = now(), "
            "error = 'worker stopped before the run finished' "
            "WHERE status = 'running' AND started_at < now() - make_interval(mins => :mins)"
        ),
        {"mins": older_than_minutes},
    )
    return result.rowcount or 0
