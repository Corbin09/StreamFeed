"""ITviec (itviec.com): public IT job listings, no login required.

A run fetches listing pages (20 jobs each) until `max_posts_per_run` job slugs are
collected, then each job's detail page. Listing cards give the precise "posted N
minutes ago" time, work mode and skill tags; the detail page's JSON-LD JobPosting gives
the description, salary text and address. The pipeline merges both by job slug.

Source config (sources.config):
    keywords:  ["python", "react"]   -> /it-jobs/python, /it-jobs/react   (empty = all)
    locations: ["ha-noi"]            -> /it-jobs/python/ha-noi             (empty = all)
    pages: 3                         max listing pages per search path
    fetch_details: true              fetch each job's detail page
    max_posts_per_run: 60
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser

from app.crawlers.base import BaseCrawler, CrawlContext, RawPage, RawPost
from app.crawlers.html import html_to_text, json_ld
from app.crawlers.registry import register

BASE_URL = "https://itviec.com"
JOB_PATH = "/it-jobs/"
SLUG_ATTR = "data-search--job-selection-job-slug-value"

_RELATIVE = re.compile(
    r"(\d+|an?|one)\s*(second|minute|min|hour|day|week|month|year)s?\s*ago"
    r"|(\d+)\s*(giây|phút|giờ|ngày|tuần|tháng|năm)\s*trước",
    re.I,
)
_UNIT_DELTA = {
    "second": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
    "giây": timedelta(seconds=1),
    "phút": timedelta(minutes=1),
    "giờ": timedelta(hours=1),
    "ngày": timedelta(days=1),
    "tuần": timedelta(weeks=1),
    "tháng": timedelta(days=30),
    "năm": timedelta(days=365),
}


def parse_relative_time(text: str, now: datetime) -> datetime | None:
    """'24 minutes ago' / 'an hour ago' / '2 ngày trước' -> absolute time relative to `now`."""
    match = _RELATIVE.search(text)
    if not match:
        return None
    if match.group(1):
        amount = match.group(1).lower()
        count = 1 if amount in {"a", "an", "one"} else int(amount)
        unit = match.group(2).lower()
    else:
        count, unit = int(match.group(3)), match.group(4).lower()
    return now - count * _UNIT_DELTA[unit]


def _text(node) -> str:
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def listing_slugs(body: str) -> list[str]:
    tree = LexborHTMLParser(body)
    return _unique([c.attributes.get(SLUG_ATTR) or "" for c in tree.css("div.job-card")])


def search_paths(config: dict) -> list[str]:
    keywords = config.get("keywords") or [None]
    locations = config.get("locations") or [None]
    return [
        "/it-jobs" + "".join(f"/{part}" for part in (kw, loc) if part)
        for kw in keywords
        for loc in locations
    ]


def _experience_months(value) -> int | None:
    """JSON-LD experienceRequirements: an object with monthsOfExperience, or free text
    such as "No requirements"."""
    if isinstance(value, dict):
        return value.get("monthsOfExperience")
    if isinstance(value, str) and "no requirement" in value.lower():
        return 0
    return None


@register
class ItviecCrawler(BaseCrawler):
    type = "itviec"
    default_max_posts = 60

    # ------------------------------------------------------------------ fetch
    def fetch(self, ctx: CrawlContext) -> Iterator[RawPage]:
        pages_per_path = int(ctx.config.get("pages", 3))
        slugs: list[str] = []
        for path in search_paths(ctx.config):
            for number in range(1, pages_per_path + 1):
                if len(slugs) >= ctx.max_posts:
                    break
                page = ctx.fetcher.get(
                    BASE_URL + path, params={"page": number} if number > 1 else None
                )
                yield page
                new = [s for s in listing_slugs(page.body) if s not in slugs]
                if not new:  # past the last page
                    break
                slugs.extend(new)

        if ctx.config.get("fetch_details", True):
            for slug in slugs[: ctx.max_posts]:
                yield ctx.fetcher.get(f"{BASE_URL}{JOB_PATH}{slug}")

    # ------------------------------------------------------------------ parse
    def parse(self, page: RawPage) -> list[RawPost]:
        tree = LexborHTMLParser(page.body)
        for item in json_ld(tree):
            if item.get("@type") == "JobPosting":
                return [self._parse_detail(page, tree, item)]
        return [self._parse_card(card, page.fetched_at) for card in tree.css("div.job-card")]

    def _parse_card(self, card, fetched_at: datetime) -> RawPost:
        slug = card.attributes.get(SLUG_ATTR) or ""
        if not slug:
            raise ValueError("job card without slug")
        title = _text(card.css_first("h3 a")) or _text(card.css_first("h3"))
        companies = [_text(a) for a in card.css("a[href*='/companies/']")]
        posted = next(
            (
                _text(s)
                for s in card.css("span.small-text")
                if "ago" in _text(s) or "trước" in _text(s)
            ),
            "",
        )
        location = card.css_first("div.text-truncate[title]")
        work_mode = card.css_first("div.small-text div.text-rich-grey.flex-shrink-0")
        expertise = card.css_first("a.text-decoration-dot-underline")
        tags = _unique(
            [_text(a) for a in card.css("a.itag[data-responsive-tag-list-target='tag']")]
        )
        if not title:
            raise ValueError(f"job card {slug} without title")
        return RawPost(
            external_id=slug,
            url=f"{BASE_URL}{JOB_PATH}{slug}",
            title=title,
            company=next((c for c in companies if c), None),
            location_raw=location.attributes.get("title") if location is not None else None,
            work_mode_raw=_text(work_mode) or None,
            tags=tags,
            posted_at=parse_relative_time(posted, fetched_at),
            extra={"expertise": _text(expertise) or None},
        )

    def _parse_detail(self, page: RawPage, tree: LexborHTMLParser, job: dict) -> RawPost:
        canonical = tree.css_first("link[rel='canonical']")
        url = canonical.attributes.get("href") if canonical is not None else page.url
        slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]

        description = html_to_text(job.get("description") or "")
        benefits = html_to_text(job.get("jobBenefits") or "")
        body = description + (f"\n\nBenefits\n{benefits}" if benefits else "")

        places = job.get("jobLocation") or []
        regions = [
            (place.get("address") or {}).get("addressRegion") or ""
            for place in (places if isinstance(places, list) else [places])
        ]
        salary = job.get("baseSalary") or {}
        salary_value = (salary.get("value") or {}).get("value")
        skills = job.get("skills") or ""
        experience = job.get("experienceRequirements")
        posted = job.get("datePosted")

        return RawPost(
            external_id=slug,
            url=f"{BASE_URL}{JOB_PATH}{slug}",
            title=" ".join((job.get("title") or "").split()),
            body=body,
            company=(job.get("hiringOrganization") or {}).get("name"),
            location_raw="; ".join(r for r in regions if r) or None,
            salary_raw=str(salary_value) if salary_value else None,
            salary_currency_hint=salary.get("currency"),
            tags=[s.strip() for s in skills.split(",") if s.strip()],
            posted_at=datetime.fromisoformat(posted).replace(tzinfo=UTC) if posted else None,
            extra={
                "employment_type": job.get("employmentType"),
                "experience_months": _experience_months(experience),
                "valid_through": job.get("validThrough"),
            },
        )
