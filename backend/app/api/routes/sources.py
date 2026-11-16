"""Source management and "Run now".

"Run now" only inserts a queued `crawl_runs` row; the worker claims and executes it.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.api.deps import SessionDep
from app.api.schemas import RunQueued, SourceOut, SourceUpdate
from app.api.serializers import run_out
from app.crawlers.base import BaseCrawler
from app.crawlers.registry import UnknownSourceType, load_builtin_crawlers
from app.models import CrawlRun, CrawlRunStatus, CrawlRunTrigger, Post, Source, SourceStatus

router = APIRouter(prefix="/sources", tags=["sources"])

ACTIVE_RUN_STATUSES = (CrawlRunStatus.QUEUED, CrawlRunStatus.RUNNING)


def _plugin(source_type: str) -> type[BaseCrawler] | None:
    try:
        return load_builtin_crawlers().get(source_type)
    except UnknownSourceType:
        return None


def _source_out(source: Source, last_run: CrawlRun | None, post_count: int) -> SourceOut:
    plugin = _plugin(source.type)
    return SourceOut(
        id=source.id,
        type=source.type,
        name=source.name,
        category=plugin.category if plugin else None,
        config=source.config or {},
        enabled=source.enabled,
        interval_minutes=source.interval_minutes,
        min_interval_minutes=plugin.min_interval_minutes if plugin else None,
        status=source.status,
        consecutive_failures=source.consecutive_failures,
        last_run_at=source.last_run_at,
        last_error=source.last_error,
        plugin_available=plugin is not None,
        post_count=post_count,
        last_run=run_out(last_run, source) if last_run else None,
    )


def _last_runs(session: SessionDep, source_ids: list[int]) -> dict[int, CrawlRun]:
    runs = session.scalars(
        select(CrawlRun)
        .where(CrawlRun.source_id.in_(source_ids))
        .ext(distinct_on(CrawlRun.source_id))
        .order_by(CrawlRun.source_id, CrawlRun.id.desc())
    )
    return {r.source_id: r for r in runs}


def _post_counts(session: SessionDep, source_ids: list[int]) -> dict[int, int]:
    rows = session.execute(
        select(Post.source_id, func.count())
        .where(Post.source_id.in_(source_ids))
        .group_by(Post.source_id)
    )
    return {sid: n for sid, n in rows}


def _get(session: SessionDep, source_id: int, *, for_update: bool = False) -> Source:
    stmt = select(Source).where(Source.id == source_id)
    if for_update:
        stmt = stmt.with_for_update()
    source = session.scalars(stmt).one_or_none()
    if source is None:
        raise HTTPException(404, "Source not found")
    return source


def _one(session: SessionDep, source: Source) -> SourceOut:
    return _source_out(
        source,
        _last_runs(session, [source.id]).get(source.id),
        _post_counts(session, [source.id]).get(source.id, 0),
    )


@router.get("", response_model=list[SourceOut])
def list_sources(session: SessionDep) -> list[SourceOut]:
    sources = session.scalars(select(Source).order_by(Source.id)).all()
    ids = [s.id for s in sources]
    runs, counts = _last_runs(session, ids), _post_counts(session, ids)
    return [_source_out(s, runs.get(s.id), counts.get(s.id, 0)) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: int, session: SessionDep) -> SourceOut:
    return _one(session, _get(session, source_id))


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(source_id: int, body: SourceUpdate, session: SessionDep) -> SourceOut:
    source = _get(session, source_id, for_update=True)
    changes = body.model_dump(exclude_unset=True)
    for name in ("enabled", "interval_minutes", "config"):
        if name in changes and changes[name] is None:
            raise HTTPException(422, f"{name} cannot be null")

    if "interval_minutes" in changes:
        plugin = _plugin(source.type)
        minimum = plugin.min_interval_minutes if plugin else 1
        if changes["interval_minutes"] < minimum:
            raise HTTPException(
                422, f"interval_minutes must be at least {minimum} for {source.type} sources"
            )
    for name, value in changes.items():
        setattr(source, name, value)
    session.commit()
    return _one(session, source)


@router.post("/{source_id}/run", response_model=RunQueued, status_code=status.HTTP_202_ACCEPTED)
def run_source(source_id: int, session: SessionDep) -> RunQueued:
    """Queue a manual run for the worker. 409 if one is already queued or running."""
    # The row lock serializes concurrent "Run now" clicks for the same source.
    source = _get(session, source_id, for_update=True)
    if _plugin(source.type) is None:
        raise HTTPException(409, f"No crawler plugin is installed for type {source.type!r}")
    if source.status == SourceStatus.NEEDS_LOGIN:
        raise HTTPException(
            409, "Source needs login: run `python -m app.crawlers.login` on the host first"
        )
    active = session.scalars(
        select(CrawlRun.id).where(
            CrawlRun.source_id == source.id, CrawlRun.status.in_(ACTIVE_RUN_STATUSES)
        )
    ).first()
    if active is not None:
        raise HTTPException(409, f"Run {active} is already queued or running for this source")

    run = CrawlRun(
        source_id=source.id, status=CrawlRunStatus.QUEUED, trigger=CrawlRunTrigger.MANUAL
    )
    session.add(run)
    session.commit()
    return RunQueued(run_id=run.id)
