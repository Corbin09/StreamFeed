"""Maps `sources.type` to crawler plugin classes."""

from __future__ import annotations

import importlib

from app.crawlers.base import BaseCrawler

# Modules that define built-in crawlers; importing them registers the plugins.
BUILTIN_MODULES: list[str] = [
    "app.crawlers.itviec",
    "app.crawlers.facebook",
]


class UnknownSourceType(LookupError):
    pass


class CrawlerRegistry:
    def __init__(self) -> None:
        self._crawlers: dict[str, type[BaseCrawler]] = {}

    def register(self, crawler: type[BaseCrawler]) -> type[BaseCrawler]:
        """Register a crawler class. Usable as a class decorator."""
        existing = self._crawlers.get(crawler.type)
        if existing is not None and existing is not crawler:
            raise ValueError(f"Crawler type {crawler.type!r} is already registered")
        self._crawlers[crawler.type] = crawler
        return crawler

    def get(self, source_type: str) -> type[BaseCrawler]:
        try:
            return self._crawlers[source_type]
        except KeyError:
            known = ", ".join(sorted(self._crawlers)) or "none"
            raise UnknownSourceType(
                f"No crawler registered for source type {source_type!r} (known: {known})"
            ) from None

    def types(self) -> list[str]:
        return sorted(self._crawlers)


registry = CrawlerRegistry()
register = registry.register


def load_builtin_crawlers() -> CrawlerRegistry:
    for module in BUILTIN_MODULES:
        importlib.import_module(module)
    return registry
