from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import SessionDep, SettingsDep
from app.api.schemas import PostDetail, PostPage, PostUpdate
from app.api.serializers import post_detail, post_item
from app.models import Post, Source
from app.schemas.attributes import attribute_filters
from app.services.search import (
    InvalidCursor,
    PostQuery,
    also_on,
    duplicate_group,
    search_posts,
)

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=PostPage)
def list_posts(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: str = "job",
    source_type: Annotated[list[str], Query()] = [],  # noqa: B006 (FastAPI copies defaults)
    language: Literal["vi", "en"] | None = None,
    salary_min_usd: Annotated[float | None, Query(ge=0)] = None,
    since: Literal["24h", "7d", "30d"] | None = None,
    saved: bool | None = None,
    include_hidden: bool = False,
    include_duplicates: bool = False,
    include_stale: bool = False,
    sort: Literal["relevance", "newest"] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PostPage:
    """Feed and search.

    Category attribute filters (see `GET /meta/categories`) are extra query parameters named
    after the attribute, e.g. `?levels=senior&levels=lead&skills=python`; any value matches.
    """
    attrs = {
        f.key: request.query_params.getlist(f.key)
        for f in attribute_filters(category)
        if f.key in request.query_params
    }
    pq = PostQuery(
        q=q,
        category=category,
        source_types=source_type,
        language=language,
        salary_min_usd=salary_min_usd,
        since=since,
        saved=saved,
        include_hidden=include_hidden,
        include_duplicates=include_duplicates,
        include_stale=include_stale,
        attributes=attrs,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    try:
        result = search_posts(session, pq, settings)
    except InvalidCursor as exc:
        raise HTTPException(400, f"Invalid cursor: {exc}") from None
    others = also_on(session, [p for p, _ in result.rows])
    return PostPage(
        items=[post_item(p, s, others[p.id]) for p, s in result.rows],
        next_cursor=result.next_cursor,
    )


def _load(session: SessionDep, post_id: int) -> tuple[Post, Source]:
    post = session.get(Post, post_id)
    if post is None:
        raise HTTPException(404, "Post not found")
    return post, session.get(Source, post.source_id)


def _detail(session: SessionDep, post: Post, source: Source) -> PostDetail:
    return post_detail(
        post, source, also_on(session, [post])[post.id], duplicate_group(session, post)
    )


@router.get("/{post_id}", response_model=PostDetail)
def get_post(post_id: int, session: SessionDep) -> PostDetail:
    post, source = _load(session, post_id)
    return _detail(session, post, source)


@router.patch("/{post_id}", response_model=PostDetail)
def update_post(post_id: int, body: PostUpdate, session: SessionDep) -> PostDetail:
    post, source = _load(session, post_id)
    for name, value in body.model_dump(exclude_none=True).items():
        setattr(post, name, value)
    session.commit()
    session.refresh(post)
    return _detail(session, post, source)
