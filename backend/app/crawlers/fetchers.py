"""Network fetchers. HttpFetcher is for public pages; BrowserFetcher (Playwright) comes later."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

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

    Retries on connection errors, timeouts, 429 and 5xx. Other 4xx responses fail at once.
    """

    def __init__(
        self,
        *,
        delay: tuple[float, float] = (2.0, 4.0),
        max_retries: int = 3,
        backoff_base: float = 2.0,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.max_retries = max_retries
        self.backoff_base = backoff_base
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
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                error: str = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 400:
                    return RawPage(
                        url=str(response.url),
                        body=response.text,
                        content_type=response.headers.get("content-type", "text/html"),
                    )
                error = f"HTTP {response.status_code} for {response.url}"
                if response.status_code not in RETRYABLE_STATUS:
                    raise FetchError(error)

            if attempt >= self.max_retries:
                raise FetchError(f"{error} (gave up after {attempt + 1} attempts)")
            wait = self.backoff_base ** (attempt + 1)
            logger.warning("Retrying %s in %.0fs: %s", url, wait, error)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()
