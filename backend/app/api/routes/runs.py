from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.schemas import RunOut
from app.api.serializers import run_out
from app.models import CrawlRun, CrawlRunStatus, Source

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[RunOut])
def list_runs(
    session: SessionDep,
    source_id: int | None = None,
    status: CrawlRunStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Most recent crawl runs first."""
    stmt = select(CrawlRun, Source).join(Source, CrawlRun.source_id == Source.id)
    if source_id is not None:
        stmt = stmt.where(CrawlRun.source_id == source_id)
    if status is not None:
        stmt = stmt.where(CrawlRun.status == status)
    rows = session.execute(stmt.order_by(CrawlRun.id.desc()).limit(limit)).all()
    return [run_out(r.CrawlRun, r.Source) for r in rows]


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: SessionDep) -> RunOut:
    run = session.get(CrawlRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run_out(run, session.get(Source, run.source_id))
