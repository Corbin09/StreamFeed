"""Daily run windows (app/scheduling.py) and how the worker uses them."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.crawlers.base import FetchError
from app.crawlers.fetchers import HttpFetcher
from app.scheduling import DailyWindow, InvalidWindow, next_daily_run, parse_window

VN = ZoneInfo("Asia/Ho_Chi_Minh")
WINDOW = DailyWindow(time(7, 0), time(9, 0))


def vn(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=VN)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("07:00-09:00", WINDOW), (" 7:00 - 9:00 ", WINDOW), (None, None), ("", None)],
)
def test_parse_window(value, expected) -> None:
    assert parse_window(value) == expected


@pytest.mark.parametrize("value", ["7-9", "09:00-07:00", "25:00-26:00", "07:00-07:00"])
def test_parse_window_rejects(value) -> None:
    with pytest.raises(InvalidWindow):
        parse_window(value)


MORNING = vn(17, 6)


def due(last_run_at=None, failures=0, now=MORNING, source_id=1):
    return next_daily_run(
        window=WINDOW,
        source_id=source_id,
        last_run_at=last_run_at,
        consecutive_failures=failures,
        now=now,
        tz=VN,
    )


def test_first_run_is_inside_todays_window() -> None:
    at = due()
    assert vn(17, 7) <= at < vn(17, 9)
    assert at == due(now=vn(17, 8))  # stable during the day


def test_missed_window_runs_as_soon_as_possible() -> None:
    assert due(now=vn(17, 15)) < vn(17, 15)  # in the past = due now


def test_after_todays_run_the_next_is_tomorrow() -> None:
    at = due(last_run_at=vn(17, 7, 30), now=vn(17, 12))
    assert vn(18, 7) <= at < vn(18, 9)


def test_manual_run_late_yesterday_still_allows_today() -> None:
    at = due(last_run_at=vn(16, 23, 50), now=vn(17, 6))
    assert vn(17, 7) <= at < vn(17, 9)


@pytest.mark.parametrize(("failures", "day"), [(1, 19), (2, 21), (3, 25), (6, 25)])
def test_failures_skip_days(failures, day) -> None:
    at = due(last_run_at=vn(17, 7, 30), failures=failures)
    assert at.date() == vn(day, 7).date()


def test_minute_differs_between_sources_and_days() -> None:
    times = {due(source_id=i).time() for i in range(1, 30)}
    assert len(times) > 1


def test_timezone_is_respected_for_utc_timestamps() -> None:
    # 00:30 UTC on the 17th is 07:30 in Vietnam: that is "today" there.
    last = datetime(2026, 11, 17, 0, 30, tzinfo=UTC)
    at = due(last_run_at=last, now=last + timedelta(hours=1))
    assert at.astimezone(VN).date() == vn(18, 7).date()


# --- fetcher: no retry on 429 when the source asks for it ----------------------------------


def test_fetcher_raises_429_with_status_when_not_retryable() -> None:
    sleeps: list[float] = []
    fetcher = HttpFetcher(
        transport=httpx.MockTransport(lambda r: httpx.Response(429)),
        sleep=sleeps.append,
        retry_status={500},
    )
    with pytest.raises(FetchError) as info:
        fetcher.get("https://example.com")
    assert info.value.status == 429
    assert sleeps == []  # no retry, no backoff
