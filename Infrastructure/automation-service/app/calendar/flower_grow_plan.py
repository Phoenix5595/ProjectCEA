"""Flower grow plan date calculator (America/Toronto)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("America/Toronto")

FLOWER_PHASE_TYPES = frozenset(
    {
        "flip_to_flower",
        "flower_stretch",
        "flower_bulk",
        "flower_ripen",
        "drying",
        "harvest",
    }
)


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
    flower_end: date
    flower_weeks: int
    include_pot_phases: bool
    clone_weeks: int = 3
    pot_weeks: int = 2
    bed_weeks: int = 2
    stretch_days: int = 21
    ripen_days: int = 21
    drying_days: int = 7
    auto_mode_transition: bool = True


def _slug_location(location: str) -> str:
    return location.lower().replace(" ", "-")


def build_flower_grow_plan(inp: FlowerGrowPlanInput) -> tuple[list[PhaseRange], str | None]:
    """Return phase ranges and optional error message."""
    flower_days = inp.flower_weeks * 7
    bulk_days = flower_days - inp.stretch_days - inp.ripen_days
    if bulk_days < 1:
        return [], (
            f"Flower length must be at least "
            f"{(inp.stretch_days + inp.ripen_days + 7) // 7} weeks "
            f"for {inp.stretch_days}d stretch + {inp.ripen_days}d ripen."
        )

    flower_end = inp.flower_end
    flower_start = flower_end - timedelta(days=flower_days - 1)
    stretch_end = flower_start + timedelta(days=inp.stretch_days - 1)
    ripen_start = flower_end - timedelta(days=inp.ripen_days - 1)
    bulk_start = stretch_end + timedelta(days=1)
    bulk_end = ripen_start - timedelta(days=1)

    phases: list[PhaseRange] = []

    if inp.include_pot_phases:
        bed_end = flower_start - timedelta(days=1)
        bed_start = bed_end - timedelta(days=inp.bed_weeks * 7 - 1)
        pot_end = bed_start - timedelta(days=1)
        pot_start = pot_end - timedelta(days=inp.pot_weeks * 7 - 1)
        clone_end = pot_start - timedelta(days=1)
        clone_start = clone_end - timedelta(days=inp.clone_weeks * 7 - 1)
        phases.extend(
            [
                PhaseRange(
                    "clone_window",
                    "Clone",
                    "Veg Room",
                    "main",
                    clone_start,
                    clone_end,
                    1,
                    None,
                    None,
                    False,
                ),
                PhaseRange(
                    "pot_veg",
                    "Pot veg",
                    "Veg Room",
                    "main",
                    pot_start,
                    pot_end,
                    2,
                    None,
                    None,
                    False,
                ),
                PhaseRange(
                    "bed_veg",
                    "Bed veg",
                    "Veg Room",
                    "main",
                    bed_start,
                    bed_end,
                    3,
                    None,
                    None,
                    False,
                ),
            ]
        )
    else:
        bed_weeks_eff = inp.bed_weeks + 2
        bed_end = flower_start - timedelta(days=1)
        bed_start = bed_end - timedelta(days=bed_weeks_eff * 7 - 1)
        phases.append(
            PhaseRange(
                "bed_veg",
                "Bed veg",
                "Veg Room",
                "main",
                bed_start,
                bed_end,
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
                flower_end,
                7,
                "flower",
                "ripen",
                auto,
            ),
            PhaseRange(
                "drying",
                "Drying",
                "Flower Room",
                "main",
                flower_end + timedelta(days=1),
                flower_end + timedelta(days=inp.drying_days),
                8,
                "drying",
                None,
                auto,
            ),
            PhaseRange(
                "harvest",
                "Harvest",
                "Flower Room",
                "main",
                flower_end + timedelta(days=inp.drying_days),
                flower_end + timedelta(days=inp.drying_days),
                9,
                None,
                None,
                False,
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
