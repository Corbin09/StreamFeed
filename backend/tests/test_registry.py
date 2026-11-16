import pytest

from app.crawlers.base import BaseCrawler
from app.crawlers.registry import CrawlerRegistry, UnknownSourceType


class DemoCrawler(BaseCrawler):
    type = "demo"

    def fetch(self, ctx):
        return iter(())

    def parse(self, page):
        return []


def test_register_and_get() -> None:
    registry = CrawlerRegistry()
    registry.register(DemoCrawler)
    registry.register(DemoCrawler)  # re-registering the same class is fine
    assert registry.get("demo") is DemoCrawler
    assert registry.types() == ["demo"]


def test_unknown_type() -> None:
    with pytest.raises(UnknownSourceType, match="'nope'"):
        CrawlerRegistry().get("nope")


def test_conflicting_registration() -> None:
    registry = CrawlerRegistry()
    registry.register(DemoCrawler)
    other = type("Other", (DemoCrawler,), {"type": "demo"})
    with pytest.raises(ValueError, match="already registered"):
        registry.register(other)
