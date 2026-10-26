from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.calendar.mode_change import (
    FlowerPlanDates,
    PendingModeChange,
    pending_mode_change,
)

PLAN_START = date(2026, 2, 9)
PLAN_END = date(2026, 5, 3)
FLOWER_START = date(2026, 2, 23)
RIPEN_END = date(2026, 4, 26)
DRYING_START = date(2026, 4, 27)


def _event(
    event_id: int,
    event_type: str,
    start: date,
    end: date | None,
    *,
    mode: str | object | None = None,
    submode: str | None = None,
    order: int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if order is not None:
        metadata["phase_order"] = order
    if mode is not None:
        metadata["target_mode_name"] = mode
    if submode is not None:
        metadata["target_submode_name"] = submode
    return {
        "id": event_id,
        "event_type": event_type,
        "start_date": start,
        "end_date": end,
        "metadata": metadata,
    }


def _generated_plan_events() -> tuple[dict[str, Any], ...]:
    """The 7 phases the builder emits for a 14/21/21/21/7 plan flowering 2026-02-23."""
    return (
        _event(1, "bed_veg", PLAN_START, date(2026, 2, 22), order=3),
        _event(
            2,
            "flip_to_flower",
            FLOWER_START,
            FLOWER_START,
            mode="flower",
            submode="stretch",
            order=4,
        ),
        _event(
            3,
            "flower_stretch",
            FLOWER_START,
            date(2026, 3, 15),
            mode="flower",
            submode="stretch",
            order=5,
        ),
        _event(
            4,
            "flower_bulk",
            date(2026, 3, 16),
            date(2026, 4, 5),
            mode="flower",
            submode="bulk",
            order=6,
        ),
        _event(
            5, "flower_ripen", date(2026, 4, 6), RIPEN_END, mode="flower", submode="ripen", order=7
        ),
        _event(6, "harvest", RIPEN_END, RIPEN_END, order=8),
        _event(7, "drying", DRYING_START, PLAN_END, mode="drying", order=9),
    )


def _plan(events: tuple[dict[str, Any], ...] | None = None) -> FlowerPlanDates:
    rows = _generated_plan_events() if events is None else events
    return FlowerPlanDates(
        events=rows,
        start_date=min(event["start_date"] for event in rows),
        end_date=max(event["end_date"] or event["start_date"] for event in rows),
    )


def _change(
    today: date, active_mode: str | None, plan: FlowerPlanDates | None = None
) -> PendingModeChange | None:
    return pending_mode_change(active_mode, _plan() if plan is None else plan, today)


def test_veg_before_the_flip_turns_drying_into_veg_since_the_plan_start() -> None:
    # Given: a waiting plan whose flip is still ahead and Drying left active.
    # When: the room is in the veg-pre window.
    change = _change(date(2026, 2, 10), "drying")

    # Then: the owner is asked to return to veg from the plan start.
    assert change == PendingModeChange(
        from_mode="drying",
        to_mode="veg",
        to_submode=None,
        event_id=None,
        since=PLAN_START,
    )


def test_past_flip_asks_veg_to_flower_with_the_latest_started_flower_target() -> None:
    # Given: Flower is not active two days after the planned flip.
    # When: the plan has reached its flower window.
    change = _change(date(2026, 2, 25), "veg")

    # Then: the tie between flip and stretch resolves to the higher phase order.
    assert change == PendingModeChange(
        from_mode="veg",
        to_mode="flower",
        to_submode="stretch",
        event_id=3,
        since=FLOWER_START,
    )


def test_past_flip_also_pulls_drying_into_flower() -> None:
    # Given: Drying left active after the planned flip.
    # When: the plan has reached its flower window.
    change = _change(date(2026, 2, 25), "drying")

    # Then: the same flower target is offered.
    assert change == PendingModeChange(
        from_mode="drying",
        to_mode="flower",
        to_submode="stretch",
        event_id=3,
        since=FLOWER_START,
    )


def test_drying_window_with_veg_active_asks_for_the_current_flower_submode() -> None:
    # Given: Veg is still active on the drying start day.
    # When: the plan expects drying.
    change = _change(DRYING_START, "veg")

    # Then: the latest-started flower target describes the current flower submode.
    assert change == PendingModeChange(
        from_mode="veg",
        to_mode="flower",
        to_submode="ripen",
        event_id=5,
        since=date(2026, 4, 6),
    )


def test_drying_window_with_flower_active_asks_for_drying_since_its_start() -> None:
    # Given: Flower is still active inside the drying window.
    # When: the plan expects drying.
    change = _change(DRYING_START, "flower")

    # Then: drying is offered from the day after ripening with its own event.
    assert change == PendingModeChange(
        from_mode="flower",
        to_mode="drying",
        to_submode=None,
        event_id=7,
        since=DRYING_START,
    )


def test_finished_plan_with_flower_active_keeps_offering_drying() -> None:
    # Given: Flower was never switched to Drying after the plan's last date.
    # When: the plan is long over.
    change = _change(date(2026, 6, 15), "flower")

    # Then: the untriggered drying change never expires.
    assert change == PendingModeChange(
        from_mode="flower",
        to_mode="drying",
        to_submode=None,
        event_id=7,
        since=DRYING_START,
    )


def test_finished_plan_with_drying_active_asks_for_veg_the_day_after_it_ends() -> None:
    # Given: Drying is still active after the plan's last date.
    # When: the day after the plan ends.
    change = _change(date(2026, 5, 4), "drying")

    # Then: veg is offered from that day, with no calendar event to point at.
    assert change == PendingModeChange(
        from_mode="drying",
        to_mode="veg",
        to_submode=None,
        event_id=None,
        since=date(2026, 5, 4),
    )


@pytest.mark.parametrize(
    ("today", "active_mode", "plan"),
    [
        (date(2026, 2, 25), "flower", _plan()),
        (date(2026, 5, 4), "veg", _plan()),
        (date(2026, 2, 10), "sleep", _plan()),
        (date(2026, 2, 10), None, _plan()),
        (date(2026, 2, 10), "drying", None),
    ],
)
def test_no_change_is_offered_outside_the_pending_table(
    today: date, active_mode: str | None, plan: FlowerPlanDates | None
) -> None:
    # When: the active mode already matches the plan or cannot be changed.
    change = pending_mode_change(active_mode, plan, today)

    # Then: no button is offered.
    assert change is None


def test_a_gap_after_ripening_still_promises_drying_from_the_next_day() -> None:
    # Given: an edited plan whose drying phase starts four days after ripening.
    events = tuple(
        event for event in _generated_plan_events() if event["event_type"] != "drying"
    ) + (_event(7, "drying", date(2026, 5, 1), date(2026, 5, 7), mode="drying", order=9),)
    plan = _plan(events)
    gap_day = date(2026, 4, 28)

    # Then: Drying already owning the room sees nothing to do inside the gap.
    assert _change(gap_day, "drying", plan) is None
    # ... Flower sees the drying change from the day after ripening.
    assert _change(gap_day, "flower", plan) == PendingModeChange(
        from_mode="flower",
        to_mode="drying",
        to_submode=None,
        event_id=7,
        since=DRYING_START,
    )
    # ... and Veg still offers the current flower submode.
    assert _change(gap_day, "veg", plan) == PendingModeChange(
        from_mode="veg",
        to_mode="flower",
        to_submode="ripen",
        event_id=5,
        since=date(2026, 4, 6),
    )
    # Drying to Veg only appears once the plan's last date has passed.
    assert _change(date(2026, 5, 7), "drying", plan) is None
    assert _change(date(2026, 5, 8), "drying", plan) == PendingModeChange(
        from_mode="drying",
        to_mode="veg",
        to_submode=None,
        event_id=None,
        since=date(2026, 5, 8),
    )


def test_a_plan_without_a_drying_target_never_promises_drying() -> None:
    # Given: a plan whose last target is flowering and with no drying phase.
    events = _generated_plan_events()[:-1]
    plan = FlowerPlanDates(events=events, start_date=PLAN_START, end_date=RIPEN_END)

    # When: the plan is over and Flower is still active.
    change = _change(date(2026, 4, 27), "flower", plan)

    # Then: no drying start or event exists to offer, so nothing is suggested.
    assert change is None


def test_targets_whose_mode_name_is_not_text_are_ignored() -> None:
    # Given: the only flower-shaped event carries a non-text target mode.
    events = (
        _event(1, "bed_veg", PLAN_START, date(2026, 2, 22), order=3),
        _event(2, "flip_to_flower", FLOWER_START, FLOWER_START, mode=3, order=4),
        _event(3, "flower_stretch", FLOWER_START, date(2026, 3, 15), order=5),
    )
    plan = FlowerPlanDates(events=events, start_date=PLAN_START, end_date=date(2026, 3, 15))

    # When: the plan is mid-stretch but no target resolves.
    change = _change(date(2026, 2, 25), "drying", plan)

    # Then: the window stays veg-pre, so Drying is asked to return to veg.
    assert change == PendingModeChange(
        from_mode="drying",
        to_mode="veg",
        to_submode=None,
        event_id=None,
        since=PLAN_START,
    )
