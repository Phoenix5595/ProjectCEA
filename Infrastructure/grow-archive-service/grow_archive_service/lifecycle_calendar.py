from __future__ import annotations

from datetime import date, datetime

from shared.grow_lifecycle import CALENDAR_TZ, add_calendar_months, toronto_midnight


def quarter_start_date(year: int, quarter: int) -> date:
    """Return the Toronto calendar date starting a calendar quarter."""
    if quarter < 1 or quarter > 4:
        raise ValueError("quarter must be between 1 and 4")
    return date(year, 3 * (quarter - 1) + 1, 1)


def quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    """Return the half-open UTC bounds of a Toronto calendar quarter."""
    start = quarter_start_date(year, quarter)
    return toronto_midnight(start), toronto_midnight(add_calendar_months(start, 3))


def quarter_of(day: date) -> tuple[int, int]:
    """Return the (year, quarter) containing a Toronto local date."""
    return day.year, (day.month - 1) // 3 + 1


def previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return the calendar quarter immediately before ``(year, quarter)``."""
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def latest_quarter_boundary(now: datetime) -> date:
    """Return the most recent Toronto Jan/Apr/Jul/Oct 1 boundary at or before ``now``."""
    local = now.astimezone(CALENDAR_TZ).date()
    return date(local.year, 3 * ((local.month - 1) // 3) + 1, 1)
