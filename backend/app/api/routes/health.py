from datetime import UTC, datetime

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep
from app.models import WorkerHeartbeat

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/worker")
def worker_health(session: SessionDep, settings: SettingsDep, response: Response) -> dict:
    """200 when a worker heartbeat is recent enough, else 503."""
    last = session.scalar(select(func.max(WorkerHeartbeat.beat_at)))
    age = (datetime.now(UTC) - last).total_seconds() if last else None
    ok = age is not None and age <= settings.worker_heartbeat_max_age_seconds
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else ("stale" if last else "missing"),
        "last_beat_at": last.isoformat() if last else None,
        "age_seconds": round(age, 1) if age is not None else None,
    }
