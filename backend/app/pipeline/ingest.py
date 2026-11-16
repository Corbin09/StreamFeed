"""Upsert RawPosts into `posts`.

Step 3 scope: direct field mapping and layer-1 dedup on (source_id, external_id).
Normalization (language, salary, location, level, skills) and cross-source dedup are
added in step 4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.crawlers.base import RawPost
from app.models import Post


@dataclass
class IngestResult:
    inserted: int = 0
    updated: int = 0

    def __iadd__(self, other: IngestResult) -> IngestResult:
        self.inserted += other.inserted
        self.updated += other.updated
        return self


def upsert_posts(
    session: Session, source_id: int, category: str, posts: Sequence[RawPost]
) -> IngestResult:
    """Insert new posts and refresh existing ones. Does not commit.

    `is_saved` / `is_hidden` are never touched, so user choices survive re-crawls.
    """
    # The same post can appear on two listing pages; Postgres rejects an
    # ON CONFLICT batch that updates one row twice, so keep the last occurrence.
    unique = {p.external_id: p for p in posts}
    if not unique:
        return IngestResult()

    rows = [
        {
            "source_id": source_id,
            "external_id": p.external_id,
            "url": p.url,
            "category": category,
            "title": p.title,
            "body": p.body,
            "company": p.company,
            "author": p.author,
            "posted_at": p.posted_at,
        }
        for p in unique.values()
    ]
    stmt = insert(Post).values(rows)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_posts_source_external",
        set_={
            "url": excluded.url,
            "category": excluded.category,
            "title": excluded.title,
            "body": excluded.body,
            "company": excluded.company,
            "author": excluded.author,
            "posted_at": func.coalesce(excluded.posted_at, Post.posted_at),
            "last_seen_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(literal_column("(xmax = 0)").label("inserted"))

    flags = session.execute(stmt).scalars().all()
    inserted = sum(1 for f in flags if f)
    return IngestResult(inserted=inserted, updated=len(flags) - inserted)
