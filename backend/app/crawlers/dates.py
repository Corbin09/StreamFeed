"""Date helpers shared by parsers."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

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


__all__ = ["parse_relative_time"]
