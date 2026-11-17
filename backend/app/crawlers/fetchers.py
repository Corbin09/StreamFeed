"""Network fetchers: HttpFetcher (httpx) for plain public pages, BrowserFetcher (Playwright
Chromium) for sites that refuse non-browser HTTP clients."""

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.crawlers.base import FetchError, RawPage

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class HttpFetcher:
    """Polite HTTP client: random delay between requests, retries with exponential backoff.

    Retries on connection errors, timeouts and `retry_status` (default: 429 and 5xx).
    Any other status >= 400 fails at once with `FetchError.status` set. Sources that must
    back off immediately when throttled pass a `retry_status` without 429.
    """

    def __init__(
        self,
        *,
        delay: tuple[float, float] = (2.0, 4.0),
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        retry_status: frozenset[int] | set[int] = frozenset(RETRYABLE_STATUS),
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.retry_status = frozenset(retry_status)
        self._sleep = sleep
        self._requests_made = 0
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                **(headers or {}),
            },
        )

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> RawPage:
        if self._requests_made:
            self._sleep(random.uniform(*self.delay))
        self._requests_made += 1

        attempt = 0
        while True:
            status: int | None = None
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                error: str = f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if response.status_code < 400:
                    return RawPage(
                        url=str(response.url),
                        body=response.text,
                        content_type=response.headers.get("content-type", "text/html"),
                    )
                error = f"HTTP {response.status_code} for {response.url}"
                if response.status_code not in self.retry_status:
                    raise FetchError(error, status)

            if attempt >= self.max_retries:
                raise FetchError(f"{error} (gave up after {attempt + 1} attempts)", status)
            wait = self.backoff_base ** (attempt + 1)
            logger.warning("Retrying %s in %.0fs: %s", url, wait, error)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()


class BrowserFetcher:
    """Loads pages in a real headless Chrome (Playwright), one tab, one request at a time.

    For sites that need a real browser (JavaScript-rendered pages, logged-in sessions).
    Same contract as HttpFetcher: `get()` returns a RawPage of the rendered document and
    raises FetchError (with `.status`) for HTTP errors; `retry_status` responses are retried
    with backoff. Crawlers that must interact (scroll, expand) use `page` and `pause()`.

    `storage_state` loads a Playwright session file (cookies + localStorage) created by the
    login CLI; with `save_state=True`, `close()` writes the refreshed session back (mode 0600)
    so cookies stay current. The browser starts on first use; the runner calls `close()`.
    """

    def __init__(
        self,
        *,
        delay: tuple[float, float] = (3.0, 8.0),
        max_retries: int = 2,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        retry_status: frozenset[int] | set[int] = frozenset({500, 502, 503, 504}),
        locale: str = "en-US",
        timezone_id: str = "Asia/Ho_Chi_Minh",
        executable_path: str | None = None,
        channel: str | None = None,
        storage_state: Path | None = None,
        save_state: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout_ms = timeout * 1000
        self.retry_status = frozenset(retry_status)
        self.locale = locale
        self.timezone_id = timezone_id
        self.executable_path = executable_path
        self.channel = channel
        self.storage_state = storage_state
        self.save_state = save_state
        self._sleep = sleep
        self._requests_made = 0
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        """The Playwright page (started on first use)."""
        return self._ensure_page()

    def pause(self, bounds: tuple[float, float] | None = None) -> None:
        """Sleep a random human-like interval (defaults to `delay`)."""
        self._sleep(random.uniform(*(bounds or self.delay)))

    def _ensure_page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                channel=self.channel or None,
                executable_path=self.executable_path or None,
            )
            state = self.storage_state
            self._context = self._browser.new_context(
                locale=self.locale,
                timezone_id=self.timezone_id,
                viewport={"width": 1366, "height": 768},
                storage_state=str(state) if state is not None and state.exists() else None,
            )
            self._page = self._context.new_page()
        return self._page

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> RawPage:
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
        if self._requests_made:
            self.pause()
        self._requests_made += 1
        page = self._ensure_page()

        attempt = 0
        while True:
            status: int | None = None
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            except Exception as exc:  # noqa: BLE001 - Playwright timeouts and network errors
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
            else:
                status = response.status if response is not None else 200
                if status < 400:
                    return RawPage(url=page.url, body=page.content(), content_type="text/html")
                error = f"HTTP {status} for {page.url}"
                if status not in self.retry_status:
                    raise FetchError(error, status)

            if attempt >= self.max_retries:
                raise FetchError(f"{error} (gave up after {attempt + 1} attempts)", status)
            wait = self.backoff_base ** (attempt + 1)
            logger.warning("Retrying %s in %.0fs: %s", url, wait, error)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        try:
            if self.save_state and self._context is not None and self.storage_state is not None:
                save_storage_state(self._context, self.storage_state)
        except Exception:  # noqa: BLE001 - never fail a run because the refresh failed
            logger.warning("Could not save the refreshed browser session", exc_info=True)
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = self._browser = self._context = self._page = None


def save_storage_state(context, path: Path) -> None:
    """Write a Playwright session file atomically with mode 0600 (it grants account access)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(context.storage_state(), fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
