from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import SessionDep, SettingsDep
from app.api.schemas import CategoryCount, SourceCount, Stats
from app.api.serializers import source_ref
from app.models import Post, Source
from app.services.search import visibility_conditions

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=Stats)
def stats(session: SessionDep, settings: SettingsDep) -> Stats:
    """Counts over the default feed (not hidden, not a duplicate, not stale)."""
    day_ago = datetime.now(UTC) - timedelta(hours=24)
    visible = visibility_conditions(settings)
    is_new = func.count().filter(Post.crawled_at >= day_ago)

    def base(*cols):
        return select(*cols).select_from(Post).join(Source, Post.source_id == Source.id)

    total, new_24h = session.execute(base(func.count(), is_new).where(*visible)).one()
    saved = session.scalar(base(func.count()).where(Post.is_saved.is_(True)))
    by_category = session.execute(
        base(Post.category, func.count())
        .where(*visible)
        .group_by(Post.category)
        .order_by(func.count().desc())
    ).all()

    counts = {
        sid: (n, new)
        for sid, n, new in session.execute(
            base(Post.source_id, func.count(), is_new).where(*visible).group_by(Post.source_id)
        )
    }
    sources = session.scalars(select(Source).order_by(Source.id)).all()
    return Stats(
        total=total,
        new_24h=new_24h,
        saved=saved,
        by_category=[CategoryCount(category=c, count=n) for c, n in by_category],
        by_source=[
            SourceCount(
                source=source_ref(s),
                count=counts.get(s.id, (0, 0))[0],
                new_24h=counts.get(s.id, (0, 0))[1],
            )
            for s in sources
        ],
    )
