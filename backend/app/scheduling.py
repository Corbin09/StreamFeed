"""Daily run windows for sources that must run at most once a day (LinkedIn).

A source opts in with `config.daily_window = "HH:MM-HH:MM"` (local time, `TIMEZONE`). Each
day it runs once, at a random but stable minute inside the window, which looks like a
person checking jobs rather than a cron job. Used by the worker (when is it due) and the
API (validate the setting).

- A manual "Run now" also counts as that day's run.
- If the window was missed (worker down), the run happens as soon as possible that day.
- Consecutive failures skip days: the next run is 2, 4, then 8 days after the last one.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_WINDOW = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


class InvalidWindow(ValueError):
    pass


@dataclass(frozen=True)
class DailyWindow:
    start: time
    end: time

    @property
    def span_minutes(self) -> int:
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"


def parse_window(value: object) -> DailyWindow | None:
    """'07:00-09:00' -> DailyWindow. None/'' -> None. Raises InvalidWindow otherwise."""
    if value is None or value == "":
        return None
    match = _WINDOW.match(str(value))
    if not match:
        raise InvalidWindow(f"daily_window must look like 07:00-09:00, got {value!r}")
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    try:
        window = DailyWindow(time(h1, m1), time(h2, m2))
    except ValueError as exc:
        raise InvalidWindow(f"daily_window has an invalid time: {value!r}") from exc
    if window.span_minutes <= 0:
        raise InvalidWindow("daily_window must end after it starts, within one day")
    return window


def run_time_on(day: date, window: DailyWindow, source_id: int, tz: ZoneInfo) -> datetime:
    """The source's run time on `day`: a minute in the window, stable for that day."""
    offset = random.Random(f"{source_id}:{day.isoformat()}").randrange(window.span_minutes)
    return datetime.combine(day, window.start, tz) + timedelta(minutes=offset)


def next_daily_run(
    *,
    window: DailyWindow,
    source_id: int,
    last_run_at: datetime | None,
    consecutive_failures: int,
    now: datetime,
    tz: ZoneInfo,
    max_skip_days: int = 8,
) -> datetime:
    """When a daily-window source should run next (may be in the past = due now)."""
    today = now.astimezone(tz).date()
    if last_run_at is None:
        day = today
    else:
        skip = min(2 ** max(consecutive_failures, 0), max_skip_days)
        day = last_run_at.astimezone(tz).date() + timedelta(days=skip)
    return run_time_on(day, window, source_id, tz)
