"""LinkedIn public ("guest") job search, no login and no account.

LinkedIn serves its logged-out job search from two HTML fragments:

- search:  /jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=&location=&f_TPR=&start=N
           10 job cards per call, paginated with `start` (the "sign in to see more" wall
           exists only in the browser UI); an empty body means the end of the results.
- detail:  /jobs-guest/jobs/api/jobPosting/<id>
           full description plus criteria (seniority, employment type, job function).

A run, once a day (see `daily_window` in the source config), walks the search for each
keyword with `f_TPR=r86400` (posted in the last 24 hours, newest first), collects job ids
up to `max_posts_per_run`, then fetches the detail page for the first
`max_details_per_run` of them. Everything else keeps the card data (title, company,
location, time, link).

Being throttled is expected and handled politely: requests are 3-8 s apart, 429 and other
block signals are never retried, and the run stops fetching at the first one, keeping what
it already has. Only a block on the very first request fails the run.

Source config (sources.config):
    keywords: ["software engineer", "developer"]   one search per keyword ("" = all jobs)
    location: "Vietnam"
    geo_id: null                  optional LinkedIn geoId, more precise than `location`
    time_range: "r86400"          f_TPR value; r86400 = last 24 hours
    pages_per_keyword: 10         max search calls per keyword (10 cards each)
    max_details_per_run: 100      detail pages per run
    max_posts_per_run: 300
    daily_window: "07:00-09:00"   run once a day in this local time window
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser

from app.crawlers.base import BaseCrawler, CrawlContext, FetchError, RawPage, RawPost
from app.crawlers.dates import parse_relative_time
from app.crawlers.fetchers import HttpFetcher
from app.crawlers.html import html_to_text
from app.crawlers.registry import register

logger = logging.getLogger(__name__)

BASE_URL = "https://www.linkedin.com"
SEARCH_URL = f"{BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = BASE_URL + "/jobs-guest/jobs/api/jobPosting/{job_id}"
VIEW_URL = BASE_URL + "/jobs/view/{job_id}"
PAGE_SIZE = 10

_URN = re.compile(r"urn:li:jobPosting:(\d+)")
_DETAIL_ID = re.compile(r"/jobPosting/(\d+)")
# Redirect targets that mean "logged-out access refused" (LinkedIn also answers 999).
_BLOCK_PATHS = ("/authwall", "/login", "/checkpoint", "/uas/login", "/signup")
BLOCK_STATUSES = {429, 999}
SKIP_STATUSES = {404, 410}  # a single closed or removed job; carry on with the next one

# LinkedIn seniority -> canonical levels, used only when the title names no level.
SENIORITY_LEVELS: dict[str, list[str]] = {
    "internship": ["intern"],
    "entry level": ["junior"],
    "associate": ["junior"],
    "mid-senior level": ["mid", "senior"],
    "director": ["manager"],
    "executive": ["manager"],
}

_SALARY_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")
_PER_YEAR = re.compile(r"/\s*(yr|year)|per year|a year|annually", re.I)
_PER_HOUR = re.compile(r"/\s*(hr|hour)|per hour|an hour", re.I)
HOURS_PER_MONTH = 160


class Blocked(FetchError):
    """LinkedIn refused a logged-out request (429, 999, or a redirect to a login wall)."""


def _text(node) -> str:
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


def search_job_ids(body: str) -> list[str]:
    """Job ids on a search fragment, in page order."""
    return list(dict.fromkeys(_URN.findall(body)))


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
    for number, unit in _SALARY_NUMBER.findall(text.replace(" ", " ")):
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


def _work_mode(*texts: str) -> str | None:
    joined = " ".join(t for t in texts if t).lower()
    for marker in ("remote", "hybrid", "on-site", "onsite"):
        if f"({marker})" in joined or f" {marker} " in f" {joined} ":
            return marker
    return None


@register
class LinkedinCrawler(BaseCrawler):
    type = "linkedin"
    label = "LinkedIn"
    default_max_posts = 300
    # Once a day at most: the schedule normally comes from config.daily_window.
    min_interval_minutes = 1440

    def create_fetcher(self) -> HttpFetcher:
        # Slower than ITviec, and 429 is a signal to stop, not to retry.
        return HttpFetcher(
            delay=(3.0, 8.0),
            retry_status={500, 502, 503, 504},
            max_retries=2,
            headers={"Accept-Language": "en-US,en;q=0.9,vi;q=0.8"},
        )

    # ------------------------------------------------------------------ fetch
    @staticmethod
    def _get(ctx: CrawlContext, url: str, params: dict | None = None) -> RawPage:
        try:
            page = ctx.fetcher.get(url, params=params)
        except FetchError as exc:
            if exc.status in BLOCK_STATUSES:
                raise Blocked(str(exc), exc.status) from exc
            raise
        path = urlparse(page.url).path
        if any(path.startswith(p) for p in _BLOCK_PATHS):
            raise Blocked(f"redirected to {path}")
        return page

    def fetch(self, ctx: CrawlContext) -> Iterator[RawPage]:
        cfg = ctx.config
        keywords = cfg.get("keywords") or [""]
        pages_per_keyword = int(cfg.get("pages_per_keyword", 10))
        max_details = int(cfg.get("max_details_per_run", 100))
        base_params = {
            "location": cfg.get("location", "Vietnam"),
            "f_TPR": cfg.get("time_range", "r86400"),
            "sortBy": "DD",
        }
        if cfg.get("geo_id"):
            base_params["geoId"] = str(cfg["geo_id"])

        ids: list[str] = []
        try:
            for keyword in keywords:
                for number in range(pages_per_keyword):
                    if len(ids) >= ctx.max_posts:
                        break
                    params = {**base_params, "keywords": keyword, "start": number * PAGE_SIZE}
                    page = self._get(ctx, SEARCH_URL, params)
                    found = search_job_ids(page.body)
                    if found:
                        yield page
                    new = [i for i in found if i not in ids]
                    if not new:  # past the last page (or only repeats)
                        break
                    ids.extend(new)
        except Blocked as exc:
            if not ids:
                raise  # blocked before anything was found: the run failed
            logger.warning("LinkedIn blocked the search after %d jobs; stopping: %s", len(ids), exc)
            return

        for job_id in ids[: min(max_details, ctx.max_posts)]:
            try:
                yield self._get(ctx, DETAIL_URL.format(job_id=job_id))
            except Blocked as exc:
                logger.warning("LinkedIn blocked detail pages; keeping card data: %s", exc)
                return
            except FetchError as exc:
                if exc.status in SKIP_STATUSES:
                    continue
                raise

    # ------------------------------------------------------------------ parse
    def parse(self, page: RawPage) -> list[RawPost]:
        tree = LexborHTMLParser(page.body)
        if tree.css_first("h2.top-card-layout__title") is not None:
            return [self._parse_detail(page, tree)]
        cards = [
            c
            for c in tree.css("[data-entity-urn]")
            if "jobPosting" in (c.attributes.get("data-entity-urn") or "")
        ]
        posts = [self._parse_card(c, page.fetched_at) for c in cards]
        return list({p.external_id: p for p in posts}.values())

    def _parse_card(self, card, fetched_at: datetime) -> RawPost:
        match = _URN.search(card.attributes.get("data-entity-urn") or "")
        if not match:
            raise ValueError("job card without a jobPosting urn")
        job_id = match.group(1)
        title = _text(card.css_first("h3.base-search-card__title"))
        if not title:
            raise ValueError(f"job card {job_id} without title")
        location = _text(card.css_first("span.job-search-card__location"))
        time_node = card.css_first("time")
        posted = parse_relative_time(_text(time_node), fetched_at)
        if posted is None and time_node is not None and time_node.attributes.get("datetime"):
            posted = datetime.fromisoformat(time_node.attributes["datetime"]).replace(tzinfo=UTC)
        salary = _text(card.css_first("span.job-search-card__salary-info"))
        return RawPost(
            external_id=job_id,
            url=VIEW_URL.format(job_id=job_id),
            title=title,
            company=_text(card.css_first("h4.base-search-card__subtitle")) or None,
            location_raw=location or None,
            work_mode_raw=_work_mode(location, title),
            salary_raw=normalize_salary(salary),
            posted_at=posted,
        )

    def _parse_detail(self, page: RawPage, tree: LexborHTMLParser) -> RawPost:
        match = _DETAIL_ID.search(page.url)
        if not match:
            raise ValueError(f"cannot find the job id in {page.url}")
        job_id = match.group(1)
        title = _text(tree.css_first("h2.top-card-layout__title"))
        location = _text(tree.css_first("span.topcard__flavor--bullet"))
        markup = tree.css_first("div.show-more-less-html__markup")
        body = html_to_text(markup.html or "") if markup is not None else ""
        # html_to_text keeps the wrapper's own text; drop LinkedIn's trailing "Show more/less".
        body = re.sub(r"\s*Show (more|less)\s*$", "", body).strip()

        criteria = {
            _text(item.css_first("h3")).lower(): _text(item.css_first("span"))
            for item in tree.css("li.description__job-criteria-item")
        }
        seniority = criteria.get("seniority level", "").lower()
        employment = criteria.get("employment type", "")
        salary = _text(tree.css_first("div.salary")) or _text(
            tree.css_first("div.compensation__salary")
        )
        return RawPost(
            external_id=job_id,
            url=VIEW_URL.format(job_id=job_id),
            title=title,
            body=body,
            company=_text(tree.css_first("a.topcard__org-name-link")) or None,
            location_raw=location or None,
            work_mode_raw=_work_mode(location, title),
            salary_raw=normalize_salary(salary),
            posted_at=parse_relative_time(
                _text(tree.css_first("span.posted-time-ago__text")), page.fetched_at
            ),
            extra={
                "levels": SENIORITY_LEVELS.get(seniority, []),
                "employment_type": employment.upper().replace("-", "_") or None,
                "expertise": criteria.get("job function") or None,
            },
        )
