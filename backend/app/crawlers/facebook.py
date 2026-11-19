"""Facebook groups, read once a day with a secondary account's saved login session.

How a run works:

1. The session is a Playwright `storage_state` file (`data/sessions/facebook.json`) made by
   `python -m app.crawlers.login facebook` on the host. Without it the run stops with
   `needs_login`.
2. For each configured group, a real Chrome (BrowserFetcher) opens the group sorted by
   newest posts, then scrolls slowly (3-8 s pauses) until `max_posts_per_group` posts are
   collected or `max_scrolls` is reached, pausing 20-60 s between groups. "See more" is
   expanded so long posts are complete.
3. Posts are read from the rendered page in the browser (text, author, permalink, time
   label, plus the article HTML for re-parsing) and saved as one JSON page per group.
   `parse()` turns that JSON into RawPosts, so parser fixes apply with `reparse`.
4. A login form, checkpoint, two-factor or "temporarily blocked" page raises NeedsLogin at
   once: the source moves to `needs_login` and is never retried until a new login.

The pipeline then drops posts that are not hiring posts (`filter_non_jobs`), and extracts
locations, salary, levels and skills from the free text.

Source config (sources.config):
    groups: ["https://www.facebook.com/groups/123456789", "vieclamit"]   URL, id or slug
    max_posts_per_group: 30
    max_scrolls: 12
    max_posts_per_run: 150
    daily_window: "07:00-09:00"
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from app.config import get_settings
from app.crawlers.base import BaseCrawler, CrawlContext, NeedsLogin, RawPage, RawPost
from app.crawlers.dates import parse_relative_time
from app.crawlers.fetchers import BrowserFetcher
from app.crawlers.registry import register
from app.pipeline.text import fold

logger = logging.getLogger(__name__)

BASE_URL = "https://www.facebook.com"
SESSION_FILE = "sessions/facebook.json"
TITLE_CHARS = 140

# Paths and page texts that mean the session is no longer usable.
_BLOCK_PATHS = ("/login", "/checkpoint", "/two_step_verification", "/recover", "/confirmemail")
_BLOCK_TEXTS = (
    "you're temporarily blocked",
    "you’re temporarily blocked",
    "ban tam thoi bi chan",
    "your account has been locked",
    "tai khoan cua ban da bi khoa",
    "confirm your identity",
    "xac nhan danh tinh",
)

# Runs in the page: expand "See more" in posts, then read every top-level post of the feed.
_EXPAND_JS = """
() => {
  let clicked = 0;
  for (const btn of document.querySelectorAll('[role="feed"] div[role="button"]')) {
    const label = (btn.innerText || '').trim().toLowerCase();
    if (label === 'see more' || label === 'xem thêm') { btn.click(); clicked++; }
  }
  return clicked;
}
"""

_EXTRACT_JS = """
() => {
  const articles = [...document.querySelectorAll('[role="feed"] [role="article"]')]
    .filter((a) => !a.parentElement.closest('[role="article"]'));  // skip comments
  return articles.map((a) => {
    const message = a.querySelector(
      '[data-ad-preview="message"], [data-ad-comet-preview="message"]');
    const author = a.querySelector('h2 a, h3 a, h4 a, strong a, h2, h3');
    const links = [...a.querySelectorAll('a[href]')].map((l) => ({
      href: l.href, text: (l.innerText || '').trim(), label: l.getAttribute('aria-label') || ''
    }));
    return {
      text: (message ? message.innerText : '').trim(),
      article_text: (a.innerText || '').trim(),
      author: author ? (author.innerText || '').trim() : '',
      links: links.slice(0, 40),
      html: a.outerHTML.slice(0, 200000),
    };
  });
}
"""

_POST_ID = re.compile(r"/groups/[^/?#]+/(?:posts|permalink)/([A-Za-z0-9]+)")
_MULTI_PERMALINK = re.compile(r"[?&]multi_permalinks=(\d+)")
_SHORT_AGE = re.compile(
    r"^\s*(\d+)\s*(m|min|mins|phút|h|hr|hrs|giờ|d|ngày|w|tuần|y|năm)\s*$", re.IGNORECASE
)
_SHORT_UNITS = {
    "m": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "phút": "minutes",
    "h": "hours",
    "hr": "hours",
    "hrs": "hours",
    "giờ": "hours",
    "d": "days",
    "ngày": "days",
    "w": "weeks",
    "tuần": "weeks",
    "y": "years",
    "năm": "years",
}
_SALARY_LINE = re.compile(
    r"(luong|salary|thu nhap|income|upto|up to|\$|usd|trieu|\d+\s*tr\b|\d+\s*m\b)", re.I
)
_WORK_MODE_LINE = re.compile(r"\b(remote|hybrid|onsite|on-site|lam tu xa|wfh)\b", re.I)


def group_slug(value: str) -> str:
    """'https://www.facebook.com/groups/123/?ref=x' / '123' / 'vieclamit' -> '123' / 'vieclamit'."""
    value = value.strip()
    if "/" not in value:
        return value
    parts = [p for p in urlparse(value).path.split("/") if p]
    if "groups" in parts and parts.index("groups") + 1 < len(parts):
        return parts[parts.index("groups") + 1]
    raise ValueError(f"not a Facebook group URL: {value!r}")


def group_url(slug: str) -> str:
    return f"{BASE_URL}/groups/{slug}/?sorting_setting=CHRONOLOGICAL"


def is_blocked(url: str, page_text: str) -> str | None:
    """Why the session cannot be used (login, checkpoint, block page), or None."""
    path = urlparse(url).path
    for marker in _BLOCK_PATHS:
        if path.startswith(marker) or f"{marker}/" in path:
            return f"redirected to {path}"
    folded = fold(page_text[:5000])
    for text in _BLOCK_TEXTS:
        if fold(text) in folded:
            return f"page says {text!r}"
    return None


def parse_post_time(label: str, now: datetime) -> datetime | None:
    """Facebook time labels: '5m', '2h', '3 giờ', '1d', 'Just now', 'Yesterday at 9:15 PM'."""
    text = " ".join((label or "").split())
    if not text:
        return None
    lowered = fold(text)
    if lowered in {"just now", "vua xong"}:
        return now
    if lowered.startswith(("yesterday", "hom qua")):
        return now - timedelta(days=1)
    match = _SHORT_AGE.match(text)
    if match:
        amount, unit = int(match.group(1)), _SHORT_UNITS[match.group(2).lower()]
        if unit == "years":
            return now - timedelta(days=365 * amount)
        return now - timedelta(**{unit: amount})
    return parse_relative_time(text, now)


def _post_id(links: list[dict]) -> tuple[str | None, str | None]:
    for link in links:
        href = link.get("href") or ""
        match = _POST_ID.search(href) or _MULTI_PERMALINK.search(href)
        if match:
            clean = href.split("?")[0] if _POST_ID.search(href) else href
            return match.group(1), clean
    return None, None


def _time_label(links: list[dict], permalink: str | None) -> str:
    """The post's time is the text (or aria-label) of its permalink link."""
    for link in links:
        if permalink and (link.get("href") or "").split("?")[0] == permalink.split("?")[0]:
            for value in (link.get("label"), link.get("text")):
                if value and len(value) <= 40:
                    return value
    return ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= TITLE_CHARS else line[: TITLE_CHARS - 1].rstrip() + "…"
    return ""


def _line_matching(text: str, pattern: re.Pattern) -> str | None:
    for line in text.splitlines():
        if pattern.search(fold(line)):
            return line.strip()[:200]
    return None


@register
class FacebookCrawler(BaseCrawler):
    type = "facebook"
    label = "Facebook"
    default_max_posts = 150
    filter_non_jobs = True  # groups mix hiring posts with questions, ads and chatter
    min_interval_minutes = 1440

    @staticmethod
    def session_path() -> Path:
        return Path(get_settings().data_dir) / SESSION_FILE

    def create_fetcher(self) -> BrowserFetcher:
        settings = get_settings()
        return BrowserFetcher(
            delay=(3.0, 8.0),
            locale="vi-VN",
            timezone_id=settings.timezone,
            channel=settings.browser_channel,
            executable_path=settings.browser_executable_path,
            storage_state=self.session_path(),
            save_state=True,  # keep the session's cookies fresh
        )

    # ------------------------------------------------------------------ fetch
    @staticmethod
    def _check(page) -> None:
        """Raise NeedsLogin on a login form, checkpoint or block page. Never retried."""
        reason = is_blocked(page.url, page.inner_text("body"))
        if reason is None and page.locator('[role="feed"]').count() == 0:
            if page.locator('input[name="email"], input[name="pass"]').count():
                reason = "login form shown"
        if reason:
            raise NeedsLogin(f"Facebook session not usable: {reason}")

    @staticmethod
    def _wait_for_feed(page) -> bool:
        try:
            page.wait_for_selector('[role="feed"] [role="article"]', timeout=20_000)
            return True
        except Exception:  # noqa: BLE001 - Playwright timeout
            return False

    def fetch(self, ctx: CrawlContext) -> Iterator[RawPage]:
        if not self.session_path().exists():
            raise NeedsLogin(
                "No Facebook session; run `python -m app.crawlers.login facebook` on the host"
            )
        groups = [group_slug(g) for g in ctx.config.get("groups") or []]
        if not groups:
            raise ValueError("No groups configured (sources.config.groups)")
        per_group = int(ctx.config.get("max_posts_per_group", 30))
        max_scrolls = int(ctx.config.get("max_scrolls", 12))
        fetcher: BrowserFetcher = ctx.fetcher

        for index, slug in enumerate(groups):
            if ctx.should_stop:
                return
            if index:
                fetcher.pause((20.0, 60.0))
            url = group_url(slug)
            fetcher.get(url)  # raises FetchError on HTTP errors
            page = fetcher.page
            if not self._wait_for_feed(page):
                self._check(page)
                # Logged in but no posts: not a member of a private group, or empty group.
                logger.warning("Facebook group %s shows no posts; skipping", slug)
                continue
            self._check(page)

            posts: dict[str, dict] = {}
            for _ in range(max_scrolls):
                fetcher.pause((1.0, 2.5))
                if page.evaluate(_EXPAND_JS):
                    fetcher.pause((1.0, 2.0))
                for item in page.evaluate(_EXTRACT_JS):
                    key = (
                        _post_id(item.get("links") or [])[0]
                        or hashlib.sha1(
                            (item.get("text") or item.get("article_text") or "")[:300].encode()
                        ).hexdigest()
                    )
                    # Later snapshots win: "See more" may have expanded the text since.
                    posts[key] = item
                if len(posts) >= per_group:
                    break
                page.mouse.wheel(0, random.randint(900, 2200))
                fetcher.pause()
                self._check(page)

            logger.info("Facebook group %s: %d posts read", slug, len(posts))
            body = {"group": slug, "url": url, "posts": list(posts.values())[:per_group]}
            yield RawPage(
                url=url,
                body=json.dumps(body, ensure_ascii=False),
                content_type="application/json",
            )

    # ------------------------------------------------------------------ parse
    def parse(self, page: RawPage) -> list[RawPost]:
        data = json.loads(page.body)
        slug = data["group"]
        out: list[RawPost] = []
        for item in data.get("posts") or []:
            text = (item.get("text") or "").strip()
            if not text:
                continue  # no message body: shared link, photo-only post, or layout change
            links = item.get("links") or []
            post_id, permalink = _post_id(links)
            external_id = (
                f"{slug}:{post_id}"
                if post_id
                else f"{slug}:h{hashlib.sha1(fold(text)[:300].encode()).hexdigest()[:16]}"
            )
            out.append(
                RawPost(
                    external_id=external_id,
                    url=permalink or group_url(slug),
                    title=_first_line(text),
                    body=text,
                    author=(item.get("author") or "").strip()[:255] or None,
                    # Free text: locations, salary and work mode come from matching lines.
                    location_raw=text,
                    salary_raw=_line_matching(text, _SALARY_LINE),
                    work_mode_raw=_line_matching(text, _WORK_MODE_LINE),
                    posted_at=parse_post_time(_time_label(links, permalink), page.fetched_at),
                )
            )
        return out
