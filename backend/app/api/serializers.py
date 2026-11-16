"""ORM rows -> API models."""

from __future__ import annotations

from app.api.schemas import DuplicateRef, PostDetail, PostItem, RunOut, SourceRef
from app.models import CrawlRun, Post, Source
from app.services.search import snippet


def source_ref(source: Source) -> SourceRef:
    return SourceRef(id=source.id, type=source.type, name=source.name)


def _num(value) -> float | None:
    return float(value) if value is not None else None


def post_item(post: Post, source: Source, also_on: list[str]) -> PostItem:
    return PostItem(
        id=post.id,
        title=post.title,
        company=post.company,
        url=post.url,
        source=source_ref(source),
        category=post.category,
        language=post.language,
        location=post.location,
        price_min=_num(post.price_min),
        price_max=_num(post.price_max),
        currency=post.currency,
        attributes=post.attributes or {},
        posted_at=post.posted_at,
        crawled_at=post.crawled_at,
        is_saved=post.is_saved,
        is_hidden=post.is_hidden,
        duplicate_of_id=post.duplicate_of_id,
        also_on=also_on,
        snippet=snippet(post.body),
    )


def post_detail(
    post: Post, source: Source, also_on: list[str], duplicates: list[tuple[Post, Source]]
) -> PostDetail:
    item = post_item(post, source, also_on)
    return PostDetail(
        **item.model_dump(),
        body=post.body,
        author=post.author,
        last_seen_at=post.last_seen_at,
        updated_at=post.updated_at,
        duplicates=[
            DuplicateRef(
                id=d.id, title=d.title, url=d.url, source=source_ref(s), posted_at=d.posted_at
            )
            for d, s in duplicates
        ],
    )


def run_out(run: CrawlRun, source: Source) -> RunOut:
    duration = None
    if run.finished_at is not None:
        duration = (run.finished_at - run.started_at).total_seconds()
    return RunOut(
        id=run.id,
        source=source_ref(source),
        status=run.status,
        trigger=run.trigger,
        raw_dir=run.raw_dir,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=duration,
        fetched=run.fetched,
        inserted=run.inserted,
        updated=run.updated,
        error=run.error,
    )
