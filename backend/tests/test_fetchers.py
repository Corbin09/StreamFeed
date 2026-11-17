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


# --- BrowserFetcher (Playwright) with a fake page: no browser is launched -----------------


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes  # ints (HTTP status) or exceptions, one per goto()
        self.visited: list[str] = []
        self.url = ""

    def goto(self, url: str, **_kwargs):
        self.visited.append(url)
        self.url = url
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    def content(self) -> str:
        return "<html><body>ok</body></html>"


def browser_fetcher(outcomes: list, sleeps: list[float], **kwargs):
    from app.crawlers.fetchers import BrowserFetcher

    fetcher = BrowserFetcher(sleep=sleeps.append, **kwargs)
    fetcher._page = FakePage(outcomes)  # skip launching Chromium
    return fetcher


def test_browser_fetcher_returns_rendered_page_and_encodes_params() -> None:
    sleeps: list[float] = []
    fetcher = browser_fetcher([200, 200], sleeps, delay=(3.0, 8.0))
    page = fetcher.get("https://example.com/search", params={"q": "kỹ sư", "start": 10})
    assert page.url == "https://example.com/search?q=k%E1%BB%B9+s%C6%B0&start=10"
    assert page.body == "<html><body>ok</body></html>"
    assert sleeps == []
    fetcher.get("https://example.com/next")
    assert len(sleeps) == 1 and 3.0 <= sleeps[0] <= 8.0


def test_browser_fetcher_does_not_retry_non_retryable_status() -> None:
    sleeps: list[float] = []
    fetcher = browser_fetcher([429], sleeps)
    with pytest.raises(FetchError) as info:
        fetcher.get("https://example.com")
    assert info.value.status == 429
    assert sleeps == []


def test_browser_fetcher_retries_5xx_and_navigation_errors() -> None:
    sleeps: list[float] = []
    fetcher = browser_fetcher([503, TimeoutError("Timeout 30000ms exceeded"), 200], sleeps)
    assert fetcher.get("https://example.com").body
    assert sleeps == [2.0, 4.0]


def test_browser_fetcher_gives_up() -> None:
    sleeps: list[float] = []
    fetcher = browser_fetcher([TimeoutError("t")] * 3, sleeps, max_retries=2)
    with pytest.raises(FetchError) as info:
        fetcher.get("https://example.com")
    assert info.value.status is None
    assert "gave up after 3 attempts" in str(info.value)


def test_browser_fetcher_close_without_browser_is_safe() -> None:
    from app.crawlers.fetchers import BrowserFetcher

    BrowserFetcher().close()


def test_save_storage_state_is_private(tmp_path) -> None:
    import json
    import stat

    from app.crawlers.fetchers import save_storage_state

    class FakeContext:
        def storage_state(self):
            return {"cookies": [{"name": "c_user", "value": "1"}], "origins": []}

    target = tmp_path / "sessions" / "facebook.json"
    save_storage_state(FakeContext(), target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["cookies"][0]["name"] == "c_user"
    assert not target.with_suffix(".tmp").exists()
