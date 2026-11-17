"""Core crawler types shared by every source plugin."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

from app.models import Category


class CrawlerError(Exception):
    """Base class for errors raised by crawlers and fetchers."""


class NeedsLogin(CrawlerError):
    """A login wall, checkpoint or captcha was detected. The run must stop; never retry."""


class FetchError(CrawlerError):
    """A request failed permanently (after retries, or with a non-retryable status)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # HTTP status of the last response, None for transport errors


@dataclass(frozen=True)
class RawPage:
    """One fetched document, exactly as received. Persisted by RawStore before parsing."""

    url: str
    body: str
    content_type: str = "text/html"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_json(self) -> bool:
        return "json" in self.content_type


@dataclass
class RawPost:
    """Source-level fields of one post, before normalization.

    Free-text fields (salary, location) are kept raw; the pipeline turns them into
    canonical values so they can be re-normalized later without re-crawling.
    """

    external_id: str
    url: str
    title: str
    body: str = ""
    company: str | None = None
    author: str | None = None
    location_raw: str | None = None  # may list several places, e.g. "Hà Nội; Hồ Chí Minh"
    salary_raw: str | None = None
    salary_currency_hint: str | None = None  # currency the source labels the salary with
    work_mode_raw: str | None = None  # e.g. "At office", "Hybrid", "Làm từ xa"
    tags: list[str] = field(default_factory=list)  # structured skill tags from the source
    posted_at: datetime | None = None
    # Source-specific values copied into posts.attributes (validated by the category schema).
    extra: dict[str, Any] = field(default_factory=dict)


class Fetcher(Protocol):
    """Anything a crawler uses to talk to the network. Closed by the runner after a run."""

    def close(self) -> None: ...


@dataclass(frozen=True)
class SourceInfo:
    """Read-only snapshot of a `sources` row handed to crawlers (no DB access in plugins)."""

    id: int
    type: str
    name: str
    config: dict[str, Any]


@dataclass
class CrawlContext:
    """Per-run state passed to `BaseCrawler.fetch`."""

    run_id: int
    source: SourceInfo
    fetcher: Any
    max_posts: int
    posts_seen: int = 0  # updated by the runner after each parsed page

    @property
    def config(self) -> dict[str, Any]:
        return self.source.config

    @property
    def should_stop(self) -> bool:
        """Crawlers check this between pages to respect `max_posts_per_run`."""
        return self.posts_seen >= self.max_posts


class BaseCrawler(ABC):
    """A source plugin. Subclasses are registered in `app.crawlers.registry`.

    - `fetch` performs network I/O and yields pages as they arrive. It should stop
      once `ctx.should_stop` is true; the runner also truncates to `ctx.max_posts`.
    - `parse` must be pure (no network, no DB): it is re-run on saved pages by
      `python -m app.crawlers reparse` and tested against HTML fixtures.
    """

    type: ClassVar[str]  # matches sources.type, e.g. "itviec"
    label: ClassVar[str | None] = None  # display name of the source type, e.g. "ITviec"
    category: ClassVar[str] = Category.JOB
    default_max_posts: ClassVar[int] = 200
    # True for mixed sources (Facebook groups): non-hiring posts are dropped by the pipeline.
    filter_non_jobs: ClassVar[bool] = False
    # Lowest interval the API accepts for sources of this type (login-based sources: 180).
    min_interval_minutes: ClassVar[int] = 30

    def create_fetcher(self) -> Fetcher:
        """Return the fetcher for one run. The runner closes it afterwards."""
        from app.crawlers.fetchers import HttpFetcher

        return HttpFetcher()

    @abstractmethod
    def fetch(self, ctx: CrawlContext) -> Iterator[RawPage]: ...

    @abstractmethod
    def parse(self, page: RawPage) -> list[RawPost]: ...
