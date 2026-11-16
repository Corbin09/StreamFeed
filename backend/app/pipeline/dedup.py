"""Cross-source duplicate detection (layer 2). Layer 1 is the (source_id, external_id) upsert."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.pipeline.text import slug

DUPLICATE_WINDOW_DAYS = 30
BODY_PREFIX = 300


def content_hash(title: str, company: str | None, body: str = "") -> str:
    """Same job, different sources -> same hash. Posts without a company (Facebook)
    hash the start of the body instead, since titles there are free text."""
    if company:
        key = f"{slug(title)}|{slug(company)}"
    else:
        key = f"body|{slug(body)[:BODY_PREFIX] or slug(title)}"
    return hashlib.sha256(key.encode()).hexdigest()


_LINK_SQL = text(
    f"""
    UPDATE posts AS p SET duplicate_of_id = (
        SELECT c.id FROM posts AS c
        WHERE c.content_hash = p.content_hash
          AND c.category = p.category
          AND c.source_id <> p.source_id
          AND c.duplicate_of_id IS NULL
          AND c.crawled_at >= p.crawled_at - interval '{DUPLICATE_WINDOW_DAYS} days'
          AND (c.crawled_at, c.id) < (p.crawled_at, p.id)
        ORDER BY c.crawled_at, c.id
        LIMIT 1
    )
    WHERE p.id = ANY(:ids)
    """
)


def link_duplicates(session: Session, post_ids: Sequence[int]) -> None:
    """Point each post at the oldest post from another source with the same content hash
    (or clear the link if there is none any more)."""
    if post_ids:
        session.execute(_LINK_SQL, {"ids": list(post_ids)})
