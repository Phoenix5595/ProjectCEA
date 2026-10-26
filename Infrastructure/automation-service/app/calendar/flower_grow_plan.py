"""Flower grow plan date calculator (America/Toronto)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Toronto")

FLOWER_PHASE_TYPES = frozenset(
    {
        "bed_veg",
        "flip_to_flower",
        "flower_stretch",
        "flower_bulk",
        "flower_ripen",
        "drying",
        "harvest",
    }
)

VEG_PHASE_TYPES = frozenset({"clone_window", "pot_veg"})
GROW_PLAN_PHASE_TYPES = VEG_PHASE_TYPES | FLOWER_PHASE_TYPES


@dataclass
class PhaseRange:
    event_type: str
    title: str
    location: str
    cluster: str
    start_date: date
    end_date: date
    phase_order: int
    target_mode_name: str | None
    target_submode_name: str | None
    auto_mode_transition: bool


@dataclass
class FlowerGrowPlanInput:
    crop_name: str
    environment: str
    anchor: str
    anchor_date: date
    bed_veg_start: date
    bed_veg_days: int = 14
    stretch_days: int = 21
    bulk_days: int = 21
    ripen_days: int = 21
    drying_days: int = 7
    pot_start: date | None = None
    clone_start: date | None = None
    auto_mode_transition: bool = True


def _slug_location(location: str) -> str:
    return location.lower().replace(" ", "-")


def flower_start_for(inp: FlowerGrowPlanInput) -> date:
    """The plan's flower start derived from its anchor date."""
    if inp.anchor == "flower_start":
        return inp.anchor_date
    if inp.anchor == "bed_veg_start":
        return inp.anchor_date + timedelta(days=inp.bed_veg_days)
    if inp.anchor == "ripen_end":
        return inp.anchor_date - timedelta(
            days=inp.stretch_days + inp.bulk_days + inp.ripen_days - 1
        )
    raise ValueError(f"unknown grow plan anchor: {inp.anchor}")


def build_flower_grow_plan(inp: FlowerGrowPlanInput) -> tuple[list[PhaseRange], str | None]:
    """Return phase ranges and optional error message."""
    if inp.anchor == "bed_veg_start" and inp.bed_veg_start != inp.anchor_date:
        return [], "bed_veg_start must equal anchor_date for the bed_veg_start anchor"
    flower_start = flower_start_for(inp)
    if inp.bed_veg_start >= flower_start:
        return [], "Bed veg must start before flower start"
    if inp.pot_start is not None and inp.pot_start >= inp.bed_veg_start:
        return [], "pot_start must be before bed_veg_start"
    if inp.clone_start is not None:
        clone_limit = inp.pot_start or inp.bed_veg_start
        if inp.clone_start >= clone_limit:
            return [], (
                "clone_start must be before pot_start"
                if inp.pot_start is not None
                else "clone_start must be before bed_veg_start"
            )

    stretch_end = flower_start + timedelta(days=inp.stretch_days - 1)
    bulk_start = stretch_end + timedelta(days=1)
    bulk_end = bulk_start + timedelta(days=inp.bulk_days - 1)
    ripen_start = bulk_end + timedelta(days=1)
    ripen_end = ripen_start + timedelta(days=inp.ripen_days - 1)

    phases: list[PhaseRange] = []
    if inp.clone_start is not None:
        clone_end = (inp.pot_start or inp.bed_veg_start) - timedelta(days=1)
        phases.append(
            PhaseRange(
                "clone_window",
                "Clone",
                "Veg Room",
                "main",
                inp.clone_start,
                clone_end,
                1,
                None,
                None,
                False,
            )
        )
    if inp.pot_start is not None:
        phases.append(
            PhaseRange(
                "pot_veg",
                "Pot veg",
                "Veg Room",
                "main",
                inp.pot_start,
                inp.bed_veg_start - timedelta(days=1),
                2,
                None,
                None,
                False,
            )
        )
    phases.append(
        PhaseRange(
            "bed_veg",
            "Bed veg",
            "Flower Room",
            "main",
            inp.bed_veg_start,
            flower_start - timedelta(days=1),
            3,
            None,
            None,
            False,
        )
    )

    auto = inp.auto_mode_transition
    phases.extend(
        [
            PhaseRange(
                "flip_to_flower",
                "Flip to flower",
                "Flower Room",
                "main",
                flower_start,
                flower_start,
                4,
                "flower",
                "stretch",
                auto,
            ),
            PhaseRange(
                "flower_stretch",
                "Stretch",
                "Flower Room",
                "main",
                flower_start,
                stretch_end,
                5,
                "flower",
                "stretch",
                auto,
            ),
            PhaseRange(
                "flower_bulk",
                "Bulk",
                "Flower Room",
                "main",
                bulk_start,
                bulk_end,
                6,
                "flower",
                "bulk",
                auto,
            ),
            PhaseRange(
                "flower_ripen",
                "Ripen",
                "Flower Room",
                "main",
                ripen_start,
                ripen_end,
                7,
                "flower",
                "ripen",
                auto,
            ),
            PhaseRange(
                "harvest",
                "Harvest",
                "Flower Room",
                "main",
                ripen_end,
                ripen_end,
                8,
                None,
                None,
                False,
            ),
            PhaseRange(
                "drying",
                "Drying",
                "Flower Room",
                "main",
                ripen_end + timedelta(days=1),
                ripen_end + timedelta(days=inp.drying_days),
                9,
                "drying",
                None,
                auto,
            ),
        ]
    )
    return phases, None


def phase_to_metadata(
    phase: PhaseRange,
    grow_plan_id: str,
    environment: str,
    crop_name: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "grow_plan_id": grow_plan_id,
        "phase_order": phase.phase_order,
        "environment": environment,
        "crop_name": crop_name,
        "auto_mode_transition": phase.auto_mode_transition,
    }
    if phase.target_mode_name:
        meta["target_mode_name"] = phase.target_mode_name
    if phase.target_submode_name:
        meta["target_submode_name"] = phase.target_submode_name
    return meta


def make_ical_uid(location: str, event_id: int) -> str:
    return f"cea-cal-{_slug_location(location)}-{event_id}@siberianjungle.local"
