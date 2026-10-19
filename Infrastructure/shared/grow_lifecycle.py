"""Pure Calendar window and retention-month helpers for grow lifecycle work."""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

CALENDAR_TZ: Final = ZoneInfo("America/Toronto")
_MISSING: Final = object()


class GrowLifecycleInputError(ValueError):
    """A Calendar boundary value cannot be represented losslessly."""

    code = "grow_dates_invalid"

@dataclass(frozen=True, slots=True)
class GrowWindow:
    """One grow-associated Calendar phase as a half-open UTC interval."""

    event_id: int
    grow_plan_id: UUID
    phase_type: str
    room: str
    start_at: datetime
    end_at: datetime
    start_date: date
    end_date: date | None
    explicit_end: bool


@dataclass(frozen=True, slots=True)
class RetentionCutoffs:
    """Toronto calendar-month cutoffs represented as UTC instants."""

    now: datetime
    veg_compress_before: datetime
    local_expiry_before: datetime


def _calendar_month_date(value: date, months: int) -> date:
    """Shift a date by calendar months, clamping to the target month's end."""
    month_index = value.year * 12 + value.month - 1 + months
    year, month_offset = divmod(month_index, 12)
    month = month_offset + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_calendar_months(value: date, months: int) -> date:
    """Add or subtract calendar months from a date with month-end clamping."""
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("value must be a date, not a datetime")
    if isinstance(months, bool) or not isinstance(months, int):
        raise TypeError("months must be an integer")
    return _calendar_month_date(value, months)


def _require_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        raise GrowLifecycleInputError(f"{field} must be a Calendar date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise GrowLifecycleInputError(f"{field} must be an ISO Calendar date") from exc
    raise GrowLifecycleInputError(f"{field} must be a Calendar date")


def _toronto_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=CALENDAR_TZ).astimezone(UTC)


def toronto_midnight(day: date) -> datetime:
    """Return the UTC instant at midnight starting ``day`` in Toronto."""
    if isinstance(day, datetime) or not isinstance(day, date):
        raise TypeError("day must be a date, not a datetime")
    return _toronto_midnight_utc(day)


def _value(record: Mapping[str, object], key: str) -> object:
    return record.get(key, _MISSING)




def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GrowLifecycleInputError(f"{field} must be a nonempty string")
    return value


def _grow_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise GrowLifecycleInputError("grow_plan_id must be a UUID") from exc
    raise GrowLifecycleInputError("grow_plan_id must be a UUID")


def calendar_windows(
    events: Sequence[Mapping[str, object]],
) -> tuple[GrowWindow, ...]:
    """Convert top-level grow-associated Calendar events to UTC phase windows.

    Events without a top-level ``grow_plan_id`` are ordinary Calendar events
    and are skipped. Notes and metadata are deliberately never inspected for
    identity. Calendar ``end_date`` is inclusive; a missing or null end date
    represents a single-day event and is marked as not explicit.
    """
    windows: list[GrowWindow] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise TypeError("events must contain mappings")

        raw_grow_id = _value(event, "grow_plan_id")
        if raw_grow_id is _MISSING or raw_grow_id is None:
            continue
        grow_plan_id = _grow_uuid(raw_grow_id)

        raw_event_id = _value(event, "id")
        if isinstance(raw_event_id, bool) or not isinstance(raw_event_id, int):
            raise GrowLifecycleInputError("event_id must be an integer")

        raw_phase = _value(event, "event_type")
        phase_type = _required_text(raw_phase, "phase_type")
        raw_room = _value(event, "location")
        room = _required_text(raw_room, "room")

        raw_start = _value(event, "start_date")
        if raw_start is _MISSING:
            raise GrowLifecycleInputError("start_date is required for a grow phase")
        start_date = _require_date(raw_start, "start_date")

        raw_end = _value(event, "end_date")
        explicit_end = raw_end is not _MISSING and raw_end is not None
        end_date = _require_date(raw_end, "end_date") if explicit_end else None
        inclusive_end = end_date if end_date is not None else start_date
        if inclusive_end < start_date:
            raise GrowLifecycleInputError("Calendar phase end_date precedes start_date")

        windows.append(
            GrowWindow(
                event_id=raw_event_id,
                grow_plan_id=grow_plan_id,
                phase_type=phase_type,
                room=room,
                start_at=_toronto_midnight_utc(start_date),
                end_at=_toronto_midnight_utc(inclusive_end + timedelta(days=1)),
                start_date=start_date,
                end_date=end_date,
                explicit_end=explicit_end,
            )
        )
    return tuple(windows)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be an aware datetime")


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    target_date = _calendar_month_date(value.date(), months)
    target = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=CALENDAR_TZ,
        fold=value.fold,
    )
    round_trip = target.astimezone(UTC).astimezone(CALENDAR_TZ)
    if round_trip.replace(tzinfo=None) != target.replace(tzinfo=None):
        raise ValueError("calendar-month cutoff falls in a nonexistent Toronto wall time")
    return target.astimezone(UTC)


def retention_cutoffs(now: datetime) -> RetentionCutoffs:
    """Return three- and twelve-calendar-month cutoffs from an aware instant.

    Cutoffs preserve the Toronto-local wall time of ``now`` and are returned
    as UTC-aware instants. Month-end and leap-day values clamp to the target
    month's final date rather than using a fixed number of days.
    """
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    _require_aware(now)
    local_now = now.astimezone(CALENDAR_TZ)
    return RetentionCutoffs(
        now=local_now.astimezone(UTC),
        veg_compress_before=_subtract_calendar_months(local_now, -3),
        local_expiry_before=_subtract_calendar_months(local_now, -12),
    )
