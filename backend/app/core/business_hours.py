"""Business-hours arithmetic over a weekly schedule.

Pure functions on a `Schedule` (7 optional day windows + an IANA timezone) so
they are trivially testable and hold no session. The two operations the rest of
the app needs:

- `is_open(schedule, at)` — out-of-office replies, AI response windows.
- `deadline(schedule, start, minutes)` — "when does N business-minutes after
  `start` fall?", which is what turns a wall-clock SLA into an honest one.

Both walk the schedule day by day in the schedule's own timezone, so DST is
handled by `zoneinfo` rather than by arithmetic on UTC offsets: a window is
defined as local wall-clock minutes, and we localise each day separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUTES_PER_DAY = 24 * 60
# Guard against a schedule that is open for so little that a long deadline would
# loop for years. 20 years of weeks is far beyond any real SLA threshold.
_MAX_DAYS_SCANNED = 366 * 20


@dataclass(frozen=True)
class DayWindow:
    """An open interval within one local day, in minutes from local midnight."""

    open_minute: int
    close_minute: int

    @property
    def minutes(self) -> int:
        return max(0, self.close_minute - self.open_minute)


@dataclass(frozen=True)
class Schedule:
    """A week of opening windows. `days[i]` is None when day i is closed;
    i is `date.weekday()` (0=Monday)."""

    timezone: str = "UTC"
    days: tuple[DayWindow | None, ...] = ()

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo("UTC")

    @property
    def is_always_open(self) -> bool:
        """A schedule with no configured days means "no restriction" — callers
        treat business hours as 24/7 rather than as permanently closed."""
        return not any(self.days)

    def window_for(self, day: date) -> DayWindow | None:
        if not self.days:
            return None
        index = day.weekday()
        if index >= len(self.days):
            return None
        return self.days[index]


ALWAYS_OPEN = Schedule()


def _aware(moment: datetime) -> datetime:
    """Naive input is read as UTC. Normalising at every entry point (rather than
    only where we need the local date) is what keeps a naive `datetime.now()`
    from being silently interpreted in the server's own timezone."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _local(schedule: Schedule, moment: datetime) -> datetime:
    return _aware(moment).astimezone(schedule.tz)


def _at_minute(schedule: Schedule, day: date, minute: int) -> datetime:
    """Localise `minute`-from-midnight on `day`. Minutes ≥ 1440 roll into the
    next day, which keeps `close_minute == 1440` (midnight) expressible."""
    extra_days, minute = divmod(minute, MINUTES_PER_DAY)
    naive = datetime.combine(day + timedelta(days=extra_days), time(minute // 60, minute % 60))
    return naive.replace(tzinfo=schedule.tz)


def is_open(schedule: Schedule, at: datetime) -> bool:
    """Whether the schedule is open at `at` (tz-aware; naive is read as UTC)."""
    if schedule.is_always_open:
        return True
    local = _local(schedule, at)
    window = schedule.window_for(local.date())
    if window is None or window.minutes <= 0:
        return False
    start = _at_minute(schedule, local.date(), window.open_minute)
    end = _at_minute(schedule, local.date(), window.close_minute)
    return start <= local < end


def elapsed_minutes(schedule: Schedule, start: datetime, end: datetime) -> float:
    """Open minutes between two instants. Returns 0 when `end <= start`."""
    start, end = _aware(start), _aware(end)
    if schedule.is_always_open:
        return max(0.0, (end - start).total_seconds() / 60)
    if end <= start:
        return 0.0
    total = 0.0
    day = _local(schedule, start).date()
    last_day = _local(schedule, end).date()
    scanned = 0
    while day <= last_day and scanned <= _MAX_DAYS_SCANNED:
        scanned += 1
        window = schedule.window_for(day)
        if window is not None and window.minutes > 0:
            opens = _at_minute(schedule, day, window.open_minute)
            closes = _at_minute(schedule, day, window.close_minute)
            overlap_start = max(opens, start)
            overlap_end = min(closes, end)
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds() / 60
        day += timedelta(days=1)
    return total


def deadline(schedule: Schedule, start: datetime, minutes: float) -> datetime:
    """The instant `minutes` open-minutes after `start`.

    A `start` outside opening hours is pulled forward to the next opening, so a
    ticket that arrives at 22:00 gets its full allowance from 09:00 rather than
    burning it overnight.
    """
    start = _aware(start)
    if schedule.is_always_open:
        return start + timedelta(minutes=minutes)
    if minutes <= 0:
        return start
    remaining = minutes
    day = _local(schedule, start).date()
    scanned = 0
    while scanned <= _MAX_DAYS_SCANNED:
        scanned += 1
        window = schedule.window_for(day)
        if window is not None and window.minutes > 0:
            opens = _at_minute(schedule, day, window.open_minute)
            closes = _at_minute(schedule, day, window.close_minute)
            cursor = max(opens, start)
            if closes > cursor:
                available = (closes - cursor).total_seconds() / 60
                if available >= remaining:
                    return cursor + timedelta(minutes=remaining)
                remaining -= available
        day += timedelta(days=1)
    # Pathological schedule (e.g. every day closed but is_always_open was false):
    # fall back to wall-clock so a caller never gets a None deadline.
    return start + timedelta(minutes=minutes)
