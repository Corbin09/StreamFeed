"""Turn RawPosts into rows of `posts`: merge, classify, normalize, validate, upsert, dedup."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.crawlers.base import RawPost
from app.models import Category, Post
from app.pipeline.classify import is_hiring_post
from app.pipeline.dedup import content_hash, link_duplicates
from app.pipeline.language import detect_language
from app.pipeline.normalize import (
    detect_levels,
    extract_skills,
    normalize_locations,
    normalize_skill_tags,
    normalize_work_mode,
)
from app.pipeline.salary import parse_salary
from app.schemas.attributes import validate_attributes

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def __iadd__(self, other: IngestResult) -> IngestResult:
        self.inserted += other.inserted
        self.updated += other.updated
        self.skipped += other.skipped
        return self


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def merge_raw_posts(posts: Sequence[RawPost]) -> list[RawPost]:
    """Combine RawPosts sharing an external_id (e.g. an ITviec listing card + its detail
    page). The first non-empty value of each field wins and later posts only fill gaps,
    so crawlers should yield their most precise page first (ITviec: card title and
    "posted 24 minutes ago" before the detail page's JSON-LD). `extra` is merged per key
    with the same rule. Order of first appearance is preserved."""
    merged: dict[str, RawPost] = {}
    for post in posts:
        current = merged.get(post.external_id)
        if current is None:
            merged[post.external_id] = replace(post, extra=dict(post.extra), tags=list(post.tags))
            continue
        updates: dict[str, Any] = {}
        for f in fields(RawPost):
            new, old = getattr(post, f.name), getattr(current, f.name)
            if f.name == "extra":
                updates["extra"] = {
                    **{k: v for k, v in new.items() if not _is_empty(v)},
                    **{k: v for k, v in old.items() if not _is_empty(v)},
                }
            elif _is_empty(old) and not _is_empty(new):
                updates[f.name] = new
        merged[post.external_id] = replace(current, **updates)
    return list(merged.values())


def build_row(source_id: int, category: str, raw: RawPost) -> dict[str, Any]:
    """Normalize one RawPost into column values. Raises ValidationError on bad attributes."""
    salary = parse_salary(raw.salary_raw, raw.salary_currency_hint)
    work_mode = normalize_work_mode(raw.work_mode_raw)
    locations = normalize_locations([raw.location_raw])
    if work_mode == "remote" and "remote" not in locations:
        locations.append("remote")

    attributes: dict[str, Any] = dict(raw.extra)
    attributes["locations"] = locations
    if salary is not None:
        attributes["salary_negotiable"] = salary.negotiable
    if raw.salary_raw:
        attributes["salary_text"] = raw.salary_raw.strip()
    if category == Category.JOB:
        attributes["levels"] = detect_levels(raw.title)
        attributes["skills"] = (
            normalize_skill_tags(raw.tags)
            if raw.tags
            else extract_skills(f"{raw.title}\n{raw.body}")
        )
        attributes["work_mode"] = work_mode

    return {
        "source_id": source_id,
        "external_id": raw.external_id,
        "url": raw.url,
        "category": category,
        "title": raw.title.strip(),
        "body": raw.body.strip(),
        "language": detect_language(f"{raw.title}\n{raw.body}"),
        "company": raw.company.strip() if raw.company else None,
        "author": raw.author,
        "location": locations[0] if locations else None,
        "price_min": salary.min if salary else None,
        "price_max": salary.max if salary else None,
        "currency": salary.currency if salary else None,
        "attributes": validate_attributes(category, attributes),
        "content_hash": content_hash(raw.title, raw.company, raw.body),
        "posted_at": raw.posted_at,
    }


# Columns refreshed when a post is seen again. is_saved / is_hidden are user choices and
# duplicate_of_id is recomputed by link_duplicates, so none of them are listed.
_UPDATABLE = [
    "url",
    "category",
    "title",
    "body",
    "language",
    "company",
    "author",
    "location",
    "price_min",
    "price_max",
    "currency",
    "attributes",
    "content_hash",
]


def process_posts(
    session: Session,
    source_id: int,
    category: str,
    posts: Sequence[RawPost],
    *,
    filter_non_jobs: bool = False,
) -> IngestResult:
    """Normalize and upsert a batch of RawPosts, then link cross-source duplicates.
    Does not commit."""
    result = IngestResult()
    rows: dict[str, dict[str, Any]] = {}
    for raw in posts:
        if filter_non_jobs and not is_hiring_post(f"{raw.title}\n{raw.body}"):
            result.skipped += 1
            continue
        try:
            rows[raw.external_id] = build_row(source_id, category, raw)
        except ValidationError as exc:
            result.skipped += 1
            logger.warning("Skipping post %s: invalid attributes: %s", raw.external_id, exc)
    if not rows:
        return result

    stmt = insert(Post).values(list(rows.values()))
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_posts_source_external",
        set_={
            **{col: getattr(excluded, col) for col in _UPDATABLE},
            "posted_at": func.coalesce(excluded.posted_at, Post.posted_at),
            "last_seen_at": func.now(),
            "updated_at": func.now(),
        },
    ).returning(Post.id, literal_column("(xmax = 0)").label("inserted"))

    returned = session.execute(stmt).all()
    result.inserted = sum(1 for r in returned if r.inserted)
    result.updated = len(returned) - result.inserted
    link_duplicates(session, [r.id for r in returned])
    return result
