"""Feed and search query for GET /posts (see ARCHITECTURE.md §6.5 and §8).

Search uses the generated `posts.search_vector` with
`plainto_tsquery('simple', f_unaccent(q))`, so Vietnamese and English match with or without
diacritics. Paging is keyset-based: the cursor encodes the last row's sort key and id.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, Float, and_, case, cast, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Post, Source
from app.schemas.attributes import attribute_filters

Sort = Literal["relevance", "newest"]

SINCE_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

# Sources whose posts are never "seen again" in a stable listing (group feeds): they expire
# by age (posted_at) instead of by last_seen_at.
EXPIRE_BY_POSTED_AT: frozenset[str] = frozenset({"facebook"})

SNIPPET_CHARS = 240


class InvalidCursor(ValueError):
    pass


@dataclass
class PostQuery:
    q: str | None = None
    category: str = "job"
    source_types: list[str] = field(default_factory=list)
    language: str | None = None
    salary_min_usd: float | None = None
    since: str | None = None
    saved: bool | None = None
    include_hidden: bool = False
    include_duplicates: bool = False
    include_stale: bool = False
    # Attribute filters keyed by attribute name; any of the values matches.
    attributes: dict[str, list[str]] = field(default_factory=dict)
    sort: Sort | None = None
    cursor: str | None = None
    limit: int = 20

    @property
    def text(self) -> str | None:
        q = (self.q or "").strip()
        return q or None

    @property
    def effective_sort(self) -> Sort:
        # Relevance needs a query; without one it falls back to newest.
        if self.text is None:
            return "newest"
        return self.sort or "relevance"


@dataclass
class SearchResult:
    rows: list[tuple[Post, Source]]
    next_cursor: str | None


def sort_timestamp():
    return func.coalesce(Post.posted_at, Post.crawled_at)


def ts_query(q: str):
    return func.plainto_tsquery("simple", func.f_unaccent(q))


# --- cursor --------------------------------------------------------------------------------


def encode_cursor(sort: Sort, key: float | datetime, post_id: int) -> str:
    k = key.isoformat() if isinstance(key, datetime) else key
    raw = json.dumps({"s": sort, "k": k, "id": post_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, sort: Sort) -> tuple[float | datetime, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if data["s"] != sort:
            raise InvalidCursor("cursor was created for a different sort order")
        post_id = int(data["id"])
        if sort == "newest":
            return datetime.fromisoformat(data["k"]), post_id
        return float(data["k"]), post_id
    except InvalidCursor:
        raise
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise InvalidCursor("malformed cursor") from exc


# --- filters -------------------------------------------------------------------------------


def visibility_conditions(
    settings: Settings,
    *,
    include_hidden: bool = False,
    include_duplicates: bool = False,
    include_stale: bool = False,
    now: datetime | None = None,
) -> list[ColumnElement[bool]]:
    """Default feed visibility: not hidden, not a cross-source duplicate, not stale.

    Expects `Source` to be joined.
    """
    conds: list[ColumnElement[bool]] = []
    if not include_hidden:
        conds.append(Post.is_hidden.is_(False))
    if not include_duplicates:
        conds.append(Post.duplicate_of_id.is_(None))
    if not include_stale:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.stale_after_days)
        conds.append(Post.last_seen_at >= cutoff)
        conds.append(or_(Source.type.not_in(EXPIRE_BY_POSTED_AT), sort_timestamp() >= cutoff))
    return conds


def price_in_usd(settings: Settings):
    """Upper end of the salary/price converted to USD (VND via the fixed USD_VND_RATE)."""
    amount = func.coalesce(Post.price_max, Post.price_min)
    return case((Post.currency == "VND", amount / settings.usd_vnd_rate), else_=amount)


def attribute_condition(key: str, is_list: bool, values: list[str]) -> ColumnElement[bool]:
    # Containment (@>) is served by the GIN index on posts.attributes.
    if is_list:
        return or_(*(Post.attributes.contains({key: [v]}) for v in values))
    return or_(*(Post.attributes.contains({key: v}) for v in values))


def _filter_conditions(pq: PostQuery, settings: Settings) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [Post.category == pq.category]
    if pq.text:
        conds.append(Post.search_vector.op("@@")(ts_query(pq.text)))
    if pq.source_types:
        conds.append(Source.type.in_(pq.source_types))
    if pq.language:
        conds.append(Post.language == pq.language)
    if pq.salary_min_usd is not None:
        conds.append(price_in_usd(settings) >= pq.salary_min_usd)
    if pq.since:
        conds.append(sort_timestamp() >= datetime.now(UTC) - SINCE_WINDOWS[pq.since])
    if pq.saved is not None:
        conds.append(Post.is_saved.is_(pq.saved))

    known = {f.key: f for f in attribute_filters(pq.category)}
    for key, values in pq.attributes.items():
        spec = known.get(key)
        values = [v for v in values if v != ""]
        if spec is None or not values:
            continue
        if spec.value_type == "boolean":
            wanted = values[-1].lower() in ("1", "true", "yes")
            cond = Post.attributes.contains({key: True})
            conds.append(cond if wanted else ~cond)
        else:
            conds.append(attribute_condition(key, spec.is_list, values))

    conds += visibility_conditions(
        settings,
        include_hidden=pq.include_hidden,
        include_duplicates=pq.include_duplicates,
        # Saved posts are kept forever, so the saved view ignores expiry.
        include_stale=pq.include_stale or pq.saved is True,
    )
    return conds


# --- query ---------------------------------------------------------------------------------


def search_posts(session: Session, pq: PostQuery, settings: Settings) -> SearchResult:
    sort = pq.effective_sort
    stmt = select(Post, Source).join(Source, Post.source_id == Source.id)
    stmt = stmt.where(*_filter_conditions(pq, settings))

    if sort == "relevance":
        key_col = cast(func.ts_rank_cd(Post.search_vector, ts_query(pq.text)), Float)
    else:
        key_col = sort_timestamp()
    stmt = stmt.add_columns(key_col.label("sort_key"))

    if pq.cursor:
        key, last_id = decode_cursor(pq.cursor, sort)
        stmt = stmt.where(tuple_(key_col, Post.id) < tuple_(key, last_id))

    stmt = stmt.order_by(key_col.desc(), Post.id.desc()).limit(pq.limit + 1)
    result = session.execute(stmt).all()

    next_cursor = None
    if len(result) > pq.limit:
        result = result[: pq.limit]
        last = result[-1]
        next_cursor = encode_cursor(sort, last.sort_key, last.Post.id)
    return SearchResult([(r.Post, r.Source) for r in result], next_cursor)


def also_on(session: Session, posts: list[Post]) -> dict[int, list[str]]:
    """For each post, the other source types in its duplicate group.

    A group is a canonical post plus every post whose duplicate_of_id points at it.
    """
    if not posts:
        return {}
    group_of = {p.id: p.duplicate_of_id or p.id for p in posts}
    group_key = func.coalesce(Post.duplicate_of_id, Post.id)
    members = session.execute(
        select(group_key, Post.id, Source.type)
        .join(Source, Post.source_id == Source.id)
        .where(group_key.in_(set(group_of.values())))
    ).all()
    by_group: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for gid, pid, stype in members:
        by_group[gid].append((pid, stype))

    own_type = {pid: stype for _, pid, stype in members}
    out: dict[int, list[str]] = {}
    for p in posts:
        mine = own_type.get(p.id)
        types = {t for pid, t in by_group[group_of[p.id]] if pid != p.id and t != mine}
        out[p.id] = sorted(types)
    return out


def duplicate_group(session: Session, post: Post) -> list[tuple[Post, Source]]:
    """The other posts of `post`'s duplicate group, oldest first."""
    gid = post.duplicate_of_id or post.id
    if post.duplicate_of_id is None:
        member = Post.duplicate_of_id == gid
    else:
        member = or_(Post.id == gid, Post.duplicate_of_id == gid)
    rows = session.execute(
        select(Post, Source)
        .join(Source, Post.source_id == Source.id)
        .where(and_(member, Post.id != post.id))
        .order_by(sort_timestamp(), Post.id)
    ).all()
    return [(r.Post, r.Source) for r in rows]


_WS = re.compile(r"\s+")


def snippet(body: str, limit: int = SNIPPET_CHARS) -> str:
    text = _WS.sub(" ", body or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


__all__ = [
    "InvalidCursor",
    "PostQuery",
    "SINCE_WINDOWS",
    "also_on",
    "duplicate_group",
    "search_posts",
    "snippet",
    "visibility_conditions",
]
