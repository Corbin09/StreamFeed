import httpx
import pytest

from app.crawlers.base import FetchError
from app.crawlers.fetchers import HttpFetcher


def make_fetcher(handler, sleeps: list[float], **kwargs) -> HttpFetcher:
    return HttpFetcher(transport=httpx.MockTransport(handler), sleep=sleeps.append, **kwargs)


def test_returns_page_with_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    page = make_fetcher(handler, sleeps).get("https://example.com/api")
    assert page.is_json
    assert page.url == "https://example.com/api"
    assert '"ok"' in page.body
    assert sleeps == []  # no delay before the first request


def test_waits_a_random_delay_between_requests() -> None:
    sleeps: list[float] = []
    fetcher = make_fetcher(lambda r: httpx.Response(200, text="x"), sleeps, delay=(2.0, 4.0))
    fetcher.get("https://example.com/1")
    fetcher.get("https://example.com/2")
    assert len(sleeps) == 1 and 2.0 <= sleeps[0] <= 4.0


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_retryable_status_with_backoff(status: int) -> None:
    responses = iter(
        [httpx.Response(status), httpx.Response(status), httpx.Response(200, text="ok")]
    )
    sleeps: list[float] = []
    page = make_fetcher(lambda r: next(responses), sleeps).get("https://example.com")
    assert page.body == "ok"
    assert sleeps == [2.0, 4.0]


def test_retries_transport_errors() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, text="ok")

    assert make_fetcher(handler, []).get("https://example.com").body == "ok"


def test_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    fetcher = make_fetcher(lambda r: httpx.Response(503), sleeps, max_retries=2)
    with pytest.raises(FetchError, match="gave up after 3 attempts"):
        fetcher.get("https://example.com")
    assert sleeps == [2.0, 4.0]


def test_does_not_retry_client_errors() -> None:
    sleeps: list[float] = []
    with pytest.raises(FetchError, match="HTTP 404"):
        make_fetcher(lambda r: httpx.Response(404), sleeps).get("https://example.com")
    assert sleeps == []
