"""Business-hours arithmetic: the maths that makes an SLA honest.

Pinned clocks throughout — these are pure functions, so no session is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.business_hours import (
    ALWAYS_OPEN,
    MINUTES_PER_DAY,
    DayWindow,
    Schedule,
    deadline,
    elapsed_minutes,
    is_open,
)

# Mon–Fri 09:00–17:00, weekend closed.
WEEKDAYS_9_5 = Schedule(
    timezone="UTC",
    days=(
        DayWindow(9 * 60, 17 * 60),
        DayWindow(9 * 60, 17 * 60),
        DayWindow(9 * 60, 17 * 60),
        DayWindow(9 * 60, 17 * 60),
        DayWindow(9 * 60, 17 * 60),
        None,
        None,
    ),
)


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


class TestIsOpen:
    def test_inside_the_window(self):
        assert is_open(WEEKDAYS_9_5, _at("2026-08-05T10:00")) is True  # Wednesday

    def test_before_opening_and_after_closing(self):
        assert is_open(WEEKDAYS_9_5, _at("2026-08-05T08:59")) is False
        assert is_open(WEEKDAYS_9_5, _at("2026-08-05T17:00")) is False  # half-open interval

    def test_weekend_is_closed(self):
        assert is_open(WEEKDAYS_9_5, _at("2026-08-08T12:00")) is False  # Saturday

    def test_empty_schedule_is_always_open(self):
        assert ALWAYS_OPEN.is_always_open is True
        assert is_open(ALWAYS_OPEN, _at("2026-08-08T03:00")) is True

    def test_naive_datetime_is_read_as_utc(self):
        assert is_open(WEEKDAYS_9_5, datetime(2026, 8, 5, 10, 0)) is True


class TestElapsedMinutes:
    def test_within_one_day(self):
        assert (
            elapsed_minutes(WEEKDAYS_9_5, _at("2026-08-05T10:00"), _at("2026-08-05T12:30")) == 150
        )

    def test_clamps_to_opening_hours(self):
        # 08:00 → 18:00 on a weekday is only the 09:00–17:00 slice.
        assert (
            elapsed_minutes(WEEKDAYS_9_5, _at("2026-08-05T08:00"), _at("2026-08-05T18:00"))
            == 8 * 60
        )

    def test_skips_the_weekend(self):
        # Friday 16:00 → Monday 10:00 = 1h Friday + 1h Monday.
        assert (
            elapsed_minutes(WEEKDAYS_9_5, _at("2026-08-07T16:00"), _at("2026-08-10T10:00")) == 120
        )

    def test_reversed_range_is_zero(self):
        assert elapsed_minutes(WEEKDAYS_9_5, _at("2026-08-05T12:00"), _at("2026-08-05T10:00")) == 0

    def test_always_open_is_wall_clock(self):
        assert elapsed_minutes(
            ALWAYS_OPEN, _at("2026-08-07T16:00"), _at("2026-08-10T10:00")
        ) == pytest.approx(66 * 60)


class TestDeadline:
    def test_simple_same_day(self):
        assert deadline(WEEKDAYS_9_5, _at("2026-08-05T10:00"), 120) == _at("2026-08-05T12:00")

    def test_rolls_over_closing_time(self):
        # 16:00 + 120 business minutes = 1h today + 1h tomorrow morning.
        assert deadline(WEEKDAYS_9_5, _at("2026-08-05T16:00"), 120) == _at("2026-08-06T10:00")

    def test_the_friday_evening_case(self):
        """The whole point of §1.1: a ticket that lands Friday 18:00 must not be
        breaching an FRT target by Monday morning."""
        due = deadline(WEEKDAYS_9_5, _at("2026-08-07T18:00"), 60)
        assert due == _at("2026-08-10T10:00")  # Monday 09:00 + 1h

    def test_start_before_opening_waits_for_the_bell(self):
        assert deadline(WEEKDAYS_9_5, _at("2026-08-05T06:00"), 30) == _at("2026-08-05T09:30")

    def test_always_open_is_wall_clock(self):
        assert deadline(ALWAYS_OPEN, _at("2026-08-07T18:00"), 60) == _at("2026-08-07T19:00")

    def test_zero_minutes_is_the_start(self):
        start = _at("2026-08-05T22:00")
        assert deadline(WEEKDAYS_9_5, start, 0) == start

    def test_all_days_closed_falls_back_to_wall_clock(self):
        """A schedule that is configured but never open must still yield a
        deadline rather than looping — otherwise one bad config hangs the scan."""
        closed = Schedule(timezone="UTC", days=(DayWindow(0, 0),) * 7)
        assert deadline(closed, _at("2026-08-05T10:00"), 60) == _at("2026-08-05T11:00")


class TestTimezones:
    def test_windows_are_local_wall_clock(self):
        berlin = Schedule(
            timezone="Europe/Berlin", days=(DayWindow(9 * 60, 17 * 60),) * 5 + (None, None)
        )
        # 08:00 UTC in August is 10:00 in Berlin (CEST) — open.
        assert is_open(berlin, _at("2026-08-05T08:00")) is True
        # 07:00 UTC is 09:00 Berlin — the moment it opens.
        assert is_open(berlin, _at("2026-08-05T07:00")) is True
        assert is_open(berlin, _at("2026-08-05T06:59")) is False

    def test_dst_boundary_keeps_local_hours(self):
        """Across the CET→CEST change the window stays 09:00–17:00 *local*, so
        the UTC instant of opening shifts by an hour rather than the window
        silently moving."""
        berlin = Schedule(
            timezone="Europe/Berlin", days=(DayWindow(9 * 60, 17 * 60),) * 5 + (None, None)
        )
        tz = ZoneInfo("Europe/Berlin")
        winter = datetime(2026, 3, 27, 9, 30, tzinfo=tz)  # CET (UTC+1), Friday
        summer = datetime(2026, 3, 31, 9, 30, tzinfo=tz)  # CEST (UTC+2), Tuesday
        assert is_open(berlin, winter) is True
        assert is_open(berlin, summer) is True
        assert winter.utcoffset() != summer.utcoffset()

    def test_unknown_timezone_degrades_to_utc(self):
        schedule = Schedule(timezone="Mars/Olympus", days=(DayWindow(9 * 60, 17 * 60),) * 7)
        assert is_open(schedule, _at("2026-08-05T10:00")) is True


def test_close_at_midnight_is_expressible():
    night = Schedule(timezone="UTC", days=(DayWindow(20 * 60, MINUTES_PER_DAY),) * 7)
    assert is_open(night, _at("2026-08-05T23:59")) is True
    assert is_open(night, _at("2026-08-05T19:59")) is False
