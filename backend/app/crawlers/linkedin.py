"""LinkedIn job search, read once a day with a secondary account's saved login session.

LinkedIn refuses logged-out automated clients (429, see ARCHITECTURE.md "Source plugins"), so
this plugin works like the Facebook one: a real Chrome with a session made by
`python -m app.crawlers.login linkedin` on the host, at a human pace, once a day.

How a run works:

1. For each configured search, open the job search (past 24 hours, newest first). The page
   lists about 25 job cards; each card is a `[role=button]` whose `componentkey` holds the
   job id (`job-card-component-ref-<id>`).
2. Click the first `max_jobs_per_search` cards one by one (3-8 s apart), wait until the
   detail pane shows that job, expand "… more" and read the pane in the browser: title,
   company, "location · 2 hours ago · N applicants", insight chips (On-site, Full-time,
   seniority, salary) and the description. 20-60 s between searches.
3. Each search is saved as one JSON page; `parse()` turns it into RawPosts, so parser fixes
   apply with `reparse`. A card whose pane did not load keeps the card's title, company and
   location.
4. A login, checkpoint or auth wall, or HTTP 429/999, raises NeedsLogin at once: the source
   moves to `needs_login` and is never retried until a new login. Nothing tries to get
   around LinkedIn's limits.

Source config (sources.config):
    searches: [{"keywords": "software engineer", "location": "Vietnam"}]
    max_jobs_per_search: 15
    max_posts_per_run: 30
    daily_window: "09:00-11:00"
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode, urlparse

from app.config import get_settings
from app.crawlers.base import BaseCrawler, CrawlContext, NeedsLogin, RawPage, RawPost
from app.crawlers.dates import parse_relative_time
from app.crawlers.fetchers import BrowserFetcher, FetchError
from app.crawlers.registry import register

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
SESSION_FILE = "sessions/linkedin.json"
CARD_KEY_PREFIX = "job-card-component-ref-"
CARD_SELECTOR = f'[role="button"][componentkey^="{CARD_KEY_PREFIX}"]'
DEFAULT_SEARCHES = [{"keywords": "software engineer", "location": "Vietnam"}]

BLOCK_STATUSES = {429, 999}
# Paths that mean the session is no longer usable.
_BLOCK_PATHS = ("/login", "/uas/", "/checkpoint", "/authwall", "/challenge")

# LinkedIn seniority -> canonical levels, used only when the title names no level.
SENIORITY_LEVELS: dict[str, list[str]] = {
    "internship": ["intern"],
    "entry level": ["junior"],
    "associate": ["junior"],
    "mid-senior level": ["mid", "senior"],
    "director": ["manager"],
    "executive": ["manager"],
}
EMPLOYMENT_TYPES = {
    "full-time": "FULL_TIME",
    "part-time": "PART_TIME",
    "contract": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "internship": "INTERN",
    "volunteer": "VOLUNTEER",
}
WORK_MODES = ("on-site", "hybrid", "remote")
# Pane lines that are buttons or notices, not job facts.
_NOISE = re.compile(
    r"^(easy apply|apply|save|saved|share|show more|… more|more)$|review time", re.I
)

_SALARY_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")
_PER_YEAR = re.compile(r"/\s*(yr|year)|per year|a year|annually", re.I)
_PER_HOUR = re.compile(r"/\s*(hr|hour)|per hour|an hour", re.I)
_SALARY_LINE = re.compile(r"(\$|₫|\bvnd\b|\busd\b)\s*\d|\d\s*(\$|₫|vnd|usd)\b", re.I)
HOURS_PER_MONTH = 160

# Runs in the page: job ids and card texts, in list order.
_CARDS_JS = r"""
() => [...document.querySelectorAll('[role="button"][componentkey^="job-card-component-ref-"]')]
  .map((el) => ({
    id: el.getAttribute('componentkey').replace('job-card-component-ref-', ''),
    lines: (el.innerText || '').split('\n').map((t) => t.trim()).filter(Boolean).slice(0, 8),
  }))
"""

# Runs in the page: the job id shown in the detail pane (null while loading).
_PANE_ID_JS = r"""
() => {
  const link = [...document.querySelectorAll('a[href*="/jobs/view/"]')]
    .find((a) => (a.innerText || '').trim().length > 3);
  const m = link ? link.href.match(/jobs\/view\/(\d+)/) : null;
  return m ? m[1] : null;
}
"""

# Runs in the page: click "… more" in the "About the job" section. Returns true if clicked.
_EXPAND_JS = r"""
() => {
  const h = [...document.querySelectorAll('h2')].find((x) => /about the job/i.test(x.innerText));
  let sec = h;
  while (sec && sec.parentElement && sec.innerText.length < 200) sec = sec.parentElement;
  if (!sec) return false;
  const more = [...sec.querySelectorAll('button')]
    .find((b) => /^(…\s*)?(see\s+)?more$/i.test((b.innerText || '').trim()));
  if (!more) return false;
  more.click();
  return true;
}
"""

# Runs in the page: read the detail pane of the selected job.
_PANE_JS = r"""
() => {
  const link = [...document.querySelectorAll('a[href*="/jobs/view/"]')]
    .find((a) => (a.innerText || '').trim().length > 3);
  if (!link) return null;
  const m = link.href.match(/jobs\/view\/(\d+)/);
  let top = link;
  while (top && top.parentElement && top.innerText.split('\n').filter(Boolean).length < 6) {
    top = top.parentElement;
  }
  const h = [...document.querySelectorAll('h2')].find((x) => /about the job/i.test(x.innerText));
  let sec = h;
  while (sec && sec.parentElement && sec.innerText.length < 200) sec = sec.parentElement;
  return {
    id: m ? m[1] : null,
    title: link.innerText.trim(),
    lines: top ? top.innerText.split('\n').map((t) => t.trim()).filter(Boolean).slice(0, 20) : [],
    description: sec ? sec.innerText : '',
  };
}
"""


def search_url(search: dict) -> str:
    params = {
        "keywords": search.get("keywords", ""),
        "location": search.get("location", "Vietnam"),
        "f_TPR": "r86400",  # posted in the past 24 hours
        "sortBy": "DD",  # newest first
    }
    return f"{BASE_URL}/jobs/search/?{urlencode(params)}"


def job_url(job_id: str) -> str:
    return f"{BASE_URL}/jobs/view/{job_id}/"


def is_blocked(url: str) -> str | None:
    path = urlparse(url).path
    for blocked in _BLOCK_PATHS:
        if path.startswith(blocked):
            return f"redirected to {blocked}"
    return None


def normalize_salary(text: str | None) -> str | None:
    """LinkedIn salary text -> a monthly range the salary parser understands.

    '$1,000.00 - $2,000.00' -> '1000 - 2000 USD'; '$60K/yr - $90K/yr' -> '5000 - 7500 USD';
    'VND 20,000,000.00/mo' -> '20000000 VND'.
    """
    if not text or not text.strip():
        return None
    upper = text.upper()
    currency = "VND" if ("VND" in upper or "₫" in text) else "USD" if "$" in text else None
    amounts: list[float] = []
    for number, unit in _SALARY_NUMBER.findall(text.replace("\xa0", " ")):
        value = float(number.replace(",", ""))
        if unit:
            value *= 1_000 if unit.lower() == "k" else 1_000_000
        amounts.append(value)
    if not amounts or currency is None:
        return text.strip()
    if _PER_YEAR.search(text):
        amounts = [a / 12 for a in amounts]
    elif _PER_HOUR.search(text):
        amounts = [a * HOURS_PER_MONTH for a in amounts]
    parts = " - ".join(f"{round(a):d}" for a in amounts[:2])
    return f"{parts} {currency}"


def clean_description(text: str) -> str:
    """Drop the section heading, the "… more" button and what follows it."""
    text = re.sub(r"^\s*About the job\s*", "", text or "")
    text = re.split(r"\n\s*…\s*more\b", text)[0]
    text = re.split(r"\n\s*(Show less|Benefits found in job post)\b", text)[0]
    return text.strip()


_AGO = re.compile(r"\bago\b|trước", re.I)


def parse_pane(pane: dict) -> dict:
    """Split the pane's top lines into company, location, posted label and insight chips.

    The job's top card reads `company`, `title`, `location · 2 hours ago · N applicants`,
    then insight chips. Once the page has scrolled, a sticky header (`title`,
    `company • location`, Save, Apply) comes first, so the lines are anchored on the
    "· ... ago ·" line rather than on the first copy of the title.
    """
    title = (pane.get("title") or "").strip()
    lines = [line.strip() for line in pane.get("lines") or [] if line.strip()]
    meta_index = next(
        (i for i, line in enumerate(lines) if " · " in line and _AGO.search(line)), None
    )
    company = location = posted = None
    insights: list[str] = []
    if meta_index is not None:
        parts = [p.strip() for p in lines[meta_index].split(" · ")]
        location = parts[0] or None
        posted = next((p for p in parts if _AGO.search(p)), None)
        if meta_index >= 2 and lines[meta_index - 1] == title:
            company = lines[meta_index - 2]
        insights = [line for line in lines[meta_index + 1 :] if not _NOISE.search(line)]
    if company is None:
        # Sticky header: "Company • Location (On-site)".
        header = next((line for line in lines if " • " in line), None)
        if header:
            company, _, rest = header.partition(" • ")
            location = location or rest.strip() or None
    return {
        "title": title,
        "company": company,
        "location": location,
        "posted": posted,
        "insights": insights,
    }


def parse_card(lines: list[str]) -> dict:
    """Card text, two layouts:
    `Selected, <title> (Verified job)` | title | company | location (mode) | status, or
    title | company | location (mode) | status | `Posted 3 hours ago`."""
    lines = [line.strip() for line in lines if line.strip()]
    posted = next((line for line in lines if _AGO.search(line)), None)
    if len(lines) >= 3 and (lines[0].startswith("Selected, ") or lines[0].startswith(lines[1])):
        lines = lines[1:]
    return {
        "title": lines[0] if lines else "",
        "company": lines[1] if len(lines) > 1 else None,
        "location": lines[2] if len(lines) > 2 else None,
        "posted": posted,
    }


@register
class LinkedinCrawler(BaseCrawler):
    type = "linkedin"
    label = "LinkedIn"
    default_max_posts = 30
    min_interval_minutes = 1440

    @staticmethod
    def session_path() -> Path:
        return Path(get_settings().data_dir) / SESSION_FILE

    def create_fetcher(self) -> BrowserFetcher:
        settings = get_settings()
        return BrowserFetcher(
            delay=(3.0, 8.0),
            locale="en-US",
            timezone_id=settings.timezone,
            channel=settings.browser_channel,
            executable_path=settings.browser_executable_path,
            storage_state=self.session_path(),
            save_state=True,  # keep the session's cookies fresh
            retry_status=frozenset(),  # never retry LinkedIn: an error is a signal to stop
        )

    # ------------------------------------------------------------------ fetch
    @staticmethod
    def _check(page) -> None:
        """Raise NeedsLogin on a login form, checkpoint or auth wall. Never retried."""
        reason = is_blocked(page.url)
        if reason is None and page.locator('input[name="session_key"]').count():
            reason = "login form shown"
        if reason:
            raise NeedsLogin(f"LinkedIn session not usable: {reason}")

    @staticmethod
    def _wait(page, selector: str) -> bool:
        try:
            page.wait_for_selector(selector, timeout=20_000)
            return True
        except Exception:  # noqa: BLE001 - Playwright timeout
            return False

    def fetch(self, ctx: CrawlContext) -> Iterator[RawPage]:
        if not self.session_path().exists():
            raise NeedsLogin(
                "No LinkedIn session; run `python -m app.crawlers.login linkedin` on the host"
            )
        searches = ctx.config.get("searches") or DEFAULT_SEARCHES
        per_search = int(ctx.config.get("max_jobs_per_search", 15))
        fetcher: BrowserFetcher = ctx.fetcher

        for index, search in enumerate(searches):
            if ctx.should_stop:
                return
            if index:
                fetcher.pause((20.0, 60.0))
            url = search_url(search)
            try:
                fetcher.get(url)
            except FetchError as exc:
                if exc.status in BLOCK_STATUSES:
                    raise NeedsLogin(f"LinkedIn refused the request (HTTP {exc.status})") from exc
                raise
            page = fetcher.page
            self._check(page)
            if not self._wait(page, CARD_SELECTOR):
                self._check(page)
                logger.warning("LinkedIn search %r shows no job cards", search)
                continue

            jobs = []
            for card in page.evaluate(_CARDS_JS)[:per_search]:
                job = {"id": card["id"], "card": card["lines"], "pane": None}
                locator = page.locator(f'[componentkey="{CARD_KEY_PREFIX}{card["id"]}"]').first
                try:
                    locator.scroll_into_view_if_needed()
                    fetcher.pause()
                    locator.click()
                    page.wait_for_function(
                        f"() => ({_PANE_ID_JS.strip()})() === {json.dumps(card['id'])}",
                        timeout=15_000,
                    )
                    if page.evaluate(_EXPAND_JS):
                        fetcher.pause((1.0, 2.5))
                    pane = page.evaluate(_PANE_JS)
                    if pane and pane.get("id") == card["id"]:
                        job["pane"] = pane
                except Exception as exc:  # noqa: BLE001 - keep the card, move on
                    self._check(page)
                    logger.info("LinkedIn job %s: detail not read (%s)", card["id"], exc)
                self._check(page)
                jobs.append(job)

            logger.info("LinkedIn search %r: %d jobs read", search, len(jobs))
            yield RawPage(
                url=url,
                body=json.dumps({"search": search, "url": url, "jobs": jobs}, ensure_ascii=False),
                content_type="application/json",
            )

    # ------------------------------------------------------------------ parse
    def parse(self, page: RawPage) -> list[RawPost]:
        data = json.loads(page.body)
        out: list[RawPost] = []
        for job in data.get("jobs") or []:
            job_id = str(job.get("id") or "")
            if not job_id.isdigit():
                continue
            card = parse_card(job.get("card") or [])
            pane = parse_pane(job["pane"]) if job.get("pane") else None
            facts = pane or {**card, "insights": []}
            title = facts["title"] or card["title"]
            if not title:
                continue
            insights = facts["insights"]
            lowered = [i.lower() for i in insights]
            location = facts["location"] or card["location"]
            work_mode = next(
                (
                    m
                    for m in WORK_MODES
                    for i in lowered + [(card["location"] or "").lower()]
                    if m in i
                ),
                None,
            )
            salary = next((i for i in insights if _SALARY_LINE.search(i)), None)
            levels = next((SENIORITY_LEVELS[i] for i in lowered if i in SENIORITY_LEVELS), [])
            employment = next((EMPLOYMENT_TYPES[i] for i in lowered if i in EMPLOYMENT_TYPES), None)
            out.append(
                RawPost(
                    external_id=job_id,
                    url=job_url(job_id),
                    title=title,
                    company=facts["company"] or card["company"],
                    body=clean_description(job["pane"]["description"]) if pane else "",
                    location_raw=location,
                    salary_raw=normalize_salary(salary),
                    work_mode_raw=work_mode,
                    posted_at=parse_relative_time(
                        facts["posted"] or card["posted"] or "", page.fetched_at
                    ),
                    extra={"levels": levels, "employment_type": employment},
                )
            )
        return out
