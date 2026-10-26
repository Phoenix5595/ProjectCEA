"""Pending manual Flower-room mode changes derived from one calendar plan (R11).

Mode changes are manual; the calendar only drives the submode of the active
mode. This module derives the ``from → to`` change the owner still has to
trigger, purely from the latest plan's dates and the active mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class FlowerPlanDates:
    """One Flower Room plan's events plus its first and last Toronto dates."""

    events: tuple[Mapping[str, Any], ...]
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class PendingModeChange:
    """A manual mode change the owner has not triggered yet."""

    from_mode: str | None
    to_mode: str
    to_submode: str | None
    event_id: int | None
    since: date


# Expected mode today (from the plan) crossed with the active mode.
_PENDING_CHANGES: dict[tuple[str, str], str] = {
    ("veg-pre", "drying"): "veg",
    ("flower", "veg"): "flower",
    ("flower", "drying"): "flower",
    ("drying", "veg"): "flower",
    ("drying", "flower"): "drying",
    ("veg-post", "flower"): "drying",
    ("veg-post", "drying"): "veg",
}


def _metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    meta = event.get("metadata")
    return meta if isinstance(meta, Mapping) else {}


def _day(event: Mapping[str, Any], field: str) -> date | None:
    value = event.get(field)
    return value if isinstance(value, date) else None


def _end(event: Mapping[str, Any]) -> date | None:
    return _day(event, "end_date") or _day(event, "start_date")


def _target_mode(event: Mapping[str, Any]) -> str | None:
    name = _metadata(event).get("target_mode_name")
    return name if isinstance(name, str) else None


def _target_submode(event: Mapping[str, Any]) -> str | None:
    name = _metadata(event).get("target_submode_name")
    return name if isinstance(name, str) else None


def _phase_order(event: Mapping[str, Any]) -> int:
    order = _metadata(event).get("phase_order")
    return order if isinstance(order, int) and not isinstance(order, bool) else 0


def _ordered(event: Mapping[str, Any]) -> tuple[date, int]:
    return (_day(event, "start_date") or date.min, _phase_order(event))


def pending_mode_change(
    active_mode: str | None, plan: FlowerPlanDates | None, today: date
) -> PendingModeChange | None:
    """The manual mode change the owner still has to trigger, if any."""
    if plan is None:
        return None
    targets = tuple(event for event in plan.events if _target_mode(event) is not None)
    flower_events = tuple(event for event in targets if _target_mode(event) == "flower")
    drying_events = tuple(event for event in targets if _target_mode(event) == "drying")
    flower_start = min(
        (day for event in flower_events if (day := _day(event, "start_date")) is not None),
        default=None,
    )
    flower_end = max(
        (day for event in flower_events if (day := _end(event)) is not None),
        default=None,
    )
    drying_date = flower_end + timedelta(days=1) if drying_events and flower_end else None

    if today > plan.end_date:
        expected = "veg-post"
    elif drying_date is not None and today >= drying_date:
        expected = "drying"
    elif flower_start is not None and today >= flower_start:
        expected = "flower"
    else:
        expected = "veg-pre"

    if active_mode is None:
        return None
    to_mode = _PENDING_CHANGES.get((expected, active_mode))
    if to_mode is None:
        return None

    if to_mode == "flower":
        upcoming = tuple(
            event for event in flower_events if (_day(event, "start_date") or date.min) <= today
        )
        if not upcoming:
            return None
        event = max(upcoming, key=_ordered)
        return PendingModeChange(
            from_mode=active_mode,
            to_mode="flower",
            to_submode=_target_submode(event),
            event_id=event.get("id") if isinstance(event.get("id"), int) else None,
            since=_day(event, "start_date") or today,
        )
    if to_mode == "drying":
        if drying_date is None:
            return None
        event = min(drying_events, key=_ordered)
        return PendingModeChange(
            from_mode=active_mode,
            to_mode="drying",
            to_submode=None,
            event_id=event.get("id") if isinstance(event.get("id"), int) else None,
            since=drying_date,
        )
    return PendingModeChange(
        from_mode=active_mode,
        to_mode="veg",
        to_submode=None,
        event_id=None,
        since=plan.start_date if expected == "veg-pre" else plan.end_date + timedelta(days=1),
    )
