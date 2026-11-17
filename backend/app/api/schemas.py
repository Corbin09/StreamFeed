"""Request and response models of the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    name: str


# --- posts -------------------------------------------------------------------------------


class PostItem(BaseModel):
    id: int
    title: str
    company: str | None
    url: str
    source: SourceRef
    category: str
    language: str | None
    location: str | None
    price_min: float | None
    price_max: float | None
    currency: str | None
    attributes: dict[str, Any]
    posted_at: datetime | None
    crawled_at: datetime
    is_saved: bool
    is_hidden: bool
    duplicate_of_id: int | None
    also_on: list[str] = Field(description="Other source types carrying the same post")
    snippet: str


class PostPage(BaseModel):
    items: list[PostItem]
    next_cursor: str | None


class DuplicateRef(BaseModel):
    id: int
    title: str
    url: str
    source: SourceRef
    posted_at: datetime | None


class PostDetail(PostItem):
    body: str
    author: str | None
    last_seen_at: datetime
    updated_at: datetime
    duplicates: list[DuplicateRef]


class PostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_saved: bool | None = None
    is_hidden: bool | None = None


# --- crawl runs ----------------------------------------------------------------------------


class RunOut(BaseModel):
    id: int
    source: SourceRef
    status: str
    trigger: str
    raw_dir: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    fetched: int
    inserted: int
    updated: int
    error: str | None


class RunQueued(BaseModel):
    run_id: int


# --- sources -------------------------------------------------------------------------------


class SourceOut(BaseModel):
    id: int
    type: str
    name: str
    category: str | None = Field(description="Category of the plugin; null if no plugin")
    config: dict[str, Any]
    enabled: bool
    interval_minutes: int
    min_interval_minutes: int | None
    status: str
    consecutive_failures: int
    last_run_at: datetime | None
    last_error: str | None
    plugin_available: bool
    post_count: int
    last_run: RunOut | None
    daily_window: str | None = Field(
        None, description="Local HH:MM-HH:MM window when the source runs once a day"
    )
    next_run_at: datetime | None = Field(
        None, description="Planned next run for daily-window sources (may be in the past)"
    )


class SourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    interval_minutes: int | None = Field(None, gt=0)
    config: dict[str, Any] | None = None


# --- stats and meta ------------------------------------------------------------------------


class CategoryCount(BaseModel):
    category: str
    count: int


class SourceCount(BaseModel):
    source: SourceRef
    count: int
    new_24h: int


class Stats(BaseModel):
    total: int = Field(description="Visible posts: not hidden and not a duplicate")
    new_24h: int = Field(description="Posts first crawled in the last 24 hours")
    saved: int
    by_category: list[CategoryCount]
    by_source: list[SourceCount]


class FilterOption(BaseModel):
    value: str
    label: str
    count: int | None = None


class FilterSpec(BaseModel):
    key: str = Field(description="GET /posts query parameter")
    label: str
    type: Literal["multi_select", "select", "boolean"]
    options: list[FilterOption]


class CategoryMeta(BaseModel):
    key: str
    label: str
    price_label: str
    post_count: int
    filters: list[FilterSpec]


class CategoriesMeta(BaseModel):
    categories: list[CategoryMeta]
    usd_vnd_rate: float = Field(description="Rate used to compare VND and USD prices")
