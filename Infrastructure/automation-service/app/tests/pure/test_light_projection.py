from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot, frozen, frozen_rows
from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
)
from app.services.light_projection import project_lights


def _snapshot(
    *,
    start: datetime,
    end: datetime,
    mode_name: str = "Flower",
    parameters: Mapping[str, object] | None = None,
    missing_parameters: bool = False,
    targets: Sequence[Mapping[str, object]] | None = None,
    programs: Sequence[Mapping[str, object]] | None = None,
    predecessors: Sequence[Mapping[str, object]] | None = None,
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        range=MonitoringRange(start=start, end=end),
        location="Veg Room",
        cluster="main",
        active_mode=frozen({"mode_id": 1, "mode_name": mode_name}),
        calendar_events=(),
        calendar_applications=(),
        climate_periods=(),
        mode_parameters=(
            None
            if missing_parameters
            else frozen(
                parameters
                if parameters is not None
                else {
                    "mode_id": 1,
                    "day_start_time": "06:00",
                    "night_start_time": "18:00",
                    "light_ramp_up_minutes": 60,
                    "light_ramp_down_minutes": 60,
                }
            )
        ),
        light_targets=frozen_rows(
            targets if targets is not None else [{"device_id": 7, "target_intensity": 50.0}]
        ),
        light_programs=frozen_rows(programs or []),
        expected_lights=frozen_rows([{"device_id": 7, "device_name": "light_1"}]),
        effective_setpoint_predecessors=frozen_rows(predecessors or []),
        ramp_anchors=(),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(),
        projection_revision=ProjectionRevision("revision"),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=end,
        runtime_snapshot_version=RuntimeSnapshotVersion(9),
    )


@pytest.mark.parametrize("mode_name", ["Drying", " sleep "])
def test_moon_authority_modes_force_moon(mode_name: str) -> None:
    # Given: a moon-authority active mode.
    snapshot = _snapshot(
        start=datetime(2026, 1, 5, tzinfo=UTC),
        end=datetime(2026, 1, 5, 2, tzinfo=UTC),
        mode_name=mode_name,
    )

    # When: its range is projected.
    result = project_lights(snapshot, now=snapshot.range.start)

    # Then: scheduled light authority stays MOON at zero.
    assert result.photoperiod[0].phase.value == "MOON"
    assert result.lights[0].points[0].value == 0.0


def test_priority_tie_breaking_selects_oldest_program() -> None:
    # Given: two simultaneously matching programs with equal priority.
    start = datetime(2026, 1, 5, 15, tzinfo=UTC)
    programs = [
        {
            "id": 2,
            "device_id": 7,
            "priority": 5,
            "created_at": datetime(2026, 1, 2, tzinfo=UTC),
            "start_time": "09:00",
            "end_time": "17:00",
            "target_intensity": 20,
        },
        {
            "id": 1,
            "device_id": 7,
            "priority": 5,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            "start_time": "09:00",
            "end_time": "17:00",
            "target_intensity": 80,
        },
    ]

    # When: projected at their overlap.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), programs=programs), now=start
    )

    # Then: the oldest program wins.
    assert result.lights[0].points[0].value == 80.0


def test_overnight_day_owner_uses_current_local_weekday() -> None:
    # Given: a Sunday-only overnight program at Toronto Monday 01:00.
    start = datetime(2026, 1, 5, 6, tzinfo=UTC)
    program = {
        "id": 1,
        "device_id": 7,
        "day_of_week": 6,
        "start_time": "22:00",
        "end_time": "02:00",
        "target_intensity": 80,
    }

    # When: projected in the after-midnight portion.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), programs=[program]), now=start
    )

    # Then: current-local-day matching rejects Sunday rather than assigning ownership to Sunday.
    assert result.lights[0].points[0].value == 0.0


def test_program_type_parity_does_not_change_matching() -> None:
    # Given: an override program during MOON.
    start = datetime(2026, 1, 5, 4, tzinfo=UTC)
    program = {
        "id": 1,
        "device_id": 7,
        "program_type": "override",
        "start_time": "22:00",
        "end_time": "02:00",
        "target_intensity": 70,
    }

    # When: projection evaluates it.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), programs=[program]), now=start
    )

    # Then: type does not alter current scheduler matching semantics.
    assert result.lights[0].points[0].value == 70.0


def test_missing_photoperiod_is_unavailable_sun_failsafe() -> None:
    # Given: no mode parameters.
    start = datetime(2026, 1, 5, tzinfo=UTC)

    # When: projection applies the runtime failsafe.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), missing_parameters=True), now=start
    )

    # Then: visualization exposes SUN but never presents it as an operational prediction.
    assert result.photoperiod[0].phase.value == "SUN"
    assert result.photoperiod[0].provenance.quality is Quality.UNAVAILABLE


def test_static_ramp_matches_scheduler_math() -> None:
    # Given: a 10%-to-50% one-hour sun ramp.
    start = datetime(2026, 1, 5, 11, 30, tzinfo=UTC)

    # When: projected at 06:30 Toronto.
    result = project_lights(_snapshot(start=start, end=start + timedelta(minutes=5)), now=start)

    # Then: the exact time-derived ramp intensity is retained.
    assert result.lights[0].points[0].value == 30.0


def test_seven_day_one_second_cycle_is_aggregated() -> None:
    # Given: a one-second on/off cycle over the maximum range.
    start = datetime(2026, 1, 5, tzinfo=UTC)
    program = {
        "id": 1,
        "device_id": 7,
        "start_time": "00:00",
        "end_time": "23:59",
        "cycle_enabled": True,
        "cycle_on_seconds": 1,
        "cycle_off_seconds": 1,
        "target_intensity": 80,
    }

    # When: the full range is projected.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(days=7), programs=[program]), now=start
    )

    # Then: the display-safe buckets preserve aggregation provenance.
    assert result.lights[0].points[0].provenance.is_aggregated is True


def test_missing_light_anchor_is_estimated() -> None:
    # Given: a valid SUN window but no per-light target row.
    start = datetime(2026, 1, 5, 12, tzinfo=UTC)

    # When: projected through the runtime's 10% anchor fallback.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), targets=[]), now=start
    )

    # Then: the fallback remains visibly estimated.
    assert result.lights[0].provenance.quality is Quality.ESTIMATED


def test_manual_command_never_seeds_exact_projection() -> None:
    # Given: a matching-version manual effective intensity predecessor.
    start = datetime(2026, 1, 5, 11, 30, tzinfo=UTC)
    predecessor = {
        "device_name": "light_1",
        "effective_light_intensity": 35,
        "timestamp": start,
        "runtime_snapshot_identity": 9,
        "authority": "manual",
    }

    # When: projection receives it.
    result = project_lights(
        _snapshot(start=start, end=start + timedelta(minutes=5), predecessors=[predecessor]),
        now=start,
    )

    # Then: manual state cannot establish scheduler parity.
    assert result.lights[0].provenance.quality is Quality.ESTIMATED
