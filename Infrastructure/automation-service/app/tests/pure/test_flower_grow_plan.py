from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.calendar.flower_grow_plan import (
    FlowerGrowPlanInput,
    PhaseRange,
    build_flower_grow_plan,
    flower_start_for,
)


def _input(**overrides: object) -> FlowerGrowPlanInput:
    values: dict[str, object] = {
        "crop_name": "Flower cycle",
        "environment": "indoor",
        "anchor": "flower_start",
        "anchor_date": date(2026, 11, 2),
        "bed_veg_start": date(2026, 10, 19),
    }
    values.update(overrides)
    return FlowerGrowPlanInput(**values)  # type: ignore[arg-type]


def _phase(phases: list[PhaseRange], event_type: str) -> PhaseRange:
    return next(phase for phase in phases if phase.event_type == event_type)


@pytest.mark.parametrize(
    ("anchor", "anchor_date"),
    [
        ("flower_start", date(2026, 11, 2)),
        ("bed_veg_start", date(2026, 10, 19)),
        ("ripen_end", date(2027, 1, 3)),
    ],
)
def test_three_equivalent_anchors_build_identical_phases(anchor: str, anchor_date: date) -> None:
    # Given: the same plan expressed from each of the three permitted anchors.
    inp = _input(anchor=anchor, anchor_date=anchor_date)

    # When: the plan is built.
    phases, error = build_flower_grow_plan(inp)

    # Then: every anchor derives the same 2026-11-02 flower start and phases.
    assert error is None
    assert flower_start_for(inp) == date(2026, 11, 2)
    assert [(p.event_type, p.start_date, p.end_date) for p in phases] == [
        ("bed_veg", date(2026, 10, 19), date(2026, 11, 1)),
        ("flip_to_flower", date(2026, 11, 2), date(2026, 11, 2)),
        ("flower_stretch", date(2026, 11, 2), date(2026, 11, 22)),
        ("flower_bulk", date(2026, 11, 23), date(2026, 12, 13)),
        ("flower_ripen", date(2026, 12, 14), date(2027, 1, 3)),
        ("harvest", date(2027, 1, 3), date(2027, 1, 3)),
        ("drying", date(2027, 1, 4), date(2027, 1, 10)),
    ]


def test_harvest_sits_on_the_last_ripen_day_and_drying_follows_it() -> None:
    # Given: the default 21/21/21 flower phases.
    phases, error = build_flower_grow_plan(_input())

    # Then: harvest is the ripen end at order 8 and drying starts the next day at order 9.
    assert error is None
    ripen = _phase(phases, "flower_ripen")
    harvest = _phase(phases, "harvest")
    drying = _phase(phases, "drying")
    assert harvest.phase_order == 8
    assert (harvest.start_date, harvest.end_date) == (ripen.end_date, ripen.end_date)
    assert harvest.target_mode_name is None
    assert drying.phase_order == 9
    assert drying.start_date == ripen.end_date + timedelta(days=1)
    assert (drying.start_date, drying.end_date) == (date(2027, 1, 4), date(2027, 1, 10))
    assert drying.location == "Flower Room"
    assert drying.target_mode_name == "drying"


def test_pot_and_clone_windows_end_before_the_bed_veg_start() -> None:
    # Given: both optional veg phases precede the bed veg start.
    phases, error = build_flower_grow_plan(
        _input(pot_start=date(2026, 9, 28), clone_start=date(2026, 9, 7))
    )

    # Then: clone ends the day before pot veg and pot veg the day before bed veg.
    assert error is None
    clone = _phase(phases, "clone_window")
    pot = _phase(phases, "pot_veg")
    assert (clone.start_date, clone.end_date, clone.phase_order) == (
        date(2026, 9, 7),
        date(2026, 9, 27),
        1,
    )
    assert (pot.start_date, pot.end_date, pot.phase_order) == (
        date(2026, 9, 28),
        date(2026, 10, 18),
        2,
    )
    assert clone.location == "Veg Room"
    assert pot.location == "Veg Room"
    assert clone.target_mode_name is None and pot.target_mode_name is None
    assert clone.auto_mode_transition is False


def test_clone_without_pot_ends_the_day_before_bed_veg() -> None:
    # Given: cloning without a pot phase.
    phases, error = build_flower_grow_plan(_input(clone_start=date(2026, 10, 1)))

    # Then: the clone window stops the day before bed veg.
    assert error is None
    clone = _phase(phases, "clone_window")
    assert (clone.start_date, clone.end_date) == (date(2026, 10, 1), date(2026, 10, 18))
    assert not any(phase.event_type == "pot_veg" for phase in phases)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"anchor": "bed_veg_start", "anchor_date": date(2026, 10, 20)},
            "bed_veg_start must equal anchor_date for the bed_veg_start anchor",
        ),
        (
            {"bed_veg_start": date(2026, 11, 2)},
            "Bed veg must start before flower start",
        ),
        (
            {"pot_start": date(2026, 10, 19)},
            "pot_start must be before bed_veg_start",
        ),
        (
            {"pot_start": date(2026, 9, 28), "clone_start": date(2026, 9, 28)},
            "clone_start must be before pot_start",
        ),
        (
            {"clone_start": date(2026, 10, 19)},
            "clone_start must be before bed_veg_start",
        ),
    ],
)
def test_build_rejects_invalid_anchors_and_ordering(
    overrides: dict[str, object], message: str
) -> None:
    # When: an impossible plan is built.
    phases, error = build_flower_grow_plan(_input(**overrides))

    # Then: no phases are emitted and the exact validation message is returned.
    assert phases == []
    assert error == message


def test_rows_without_a_target_never_automate_a_mode_transition() -> None:
    # Given: a plan opted out of automatic transitions.
    phases, error = build_flower_grow_plan(_input(auto_mode_transition=False))

    # Then: the flower and drying targets inherit the opt-out and untargeted rows stay off.
    assert error is None
    assert _phase(phases, "flower_stretch").auto_mode_transition is False
    assert _phase(phases, "drying").auto_mode_transition is False
    assert _phase(phases, "bed_veg").auto_mode_transition is False
    assert _phase(phases, "harvest").auto_mode_transition is False


def test_target_phases_carry_their_submode_and_requested_automation() -> None:
    # Given: the default plan with automatic submode changes enabled.
    phases, error = build_flower_grow_plan(_input())

    # Then: each flower phase targets its own submode of the flower mode.
    assert error is None
    targets = {
        phase.event_type: (phase.target_mode_name, phase.target_submode_name)
        for phase in phases
        if phase.target_mode_name is not None
    }
    assert targets == {
        "flip_to_flower": ("flower", "stretch"),
        "flower_stretch": ("flower", "stretch"),
        "flower_bulk": ("flower", "bulk"),
        "flower_ripen": ("flower", "ripen"),
        "drying": ("drying", None),
    }
    assert all(
        phase.auto_mode_transition is True for phase in phases if phase.target_mode_name is not None
    )


def test_bed_veg_is_fourteen_days_by_default() -> None:
    # Given: the default bed veg input values.
    inp = _input(anchor="bed_veg_start", anchor_date=date(2026, 10, 19))

    # Then: bed veg defaults to 14 days and ends the day before flower.
    assert inp.bed_veg_days == 14
    phases, error = build_flower_grow_plan(inp)
    assert error is None
    bed_veg = _phase(phases, "bed_veg")
    assert (bed_veg.start_date, bed_veg.end_date) == (
        date(2026, 10, 19),
        date(2026, 11, 1),
    )
