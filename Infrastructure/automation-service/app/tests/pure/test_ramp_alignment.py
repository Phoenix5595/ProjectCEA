"""Alignment contract between the runtime ramp engine and the schedule trajectory.

The chart's trajectory is the contract: ramps resume from the live interpolated
value when a period change interrupts them, and their anchor is the scheduled
period boundary rather than the tick that observed the change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import pytest

from app.control.setpoint_manager import RampState, SetpointManager
from app.control.scheduler import LOCAL_TZ


class FakePeriodsRepo:
    def __init__(self, periods: list[dict[str, Any]]) -> None:
        self.periods = periods

    async def get_periods(self, location: str, cluster: str) -> list[dict[str, Any]]:
        return self.periods


class FakeDatabase:
    def __init__(self, periods: list[dict[str, Any]]) -> None:
        self.climate_periods_repo = FakePeriodsRepo(periods)


def _manager() -> SetpointManager:
    # Previous period's table values: heating 22, cooling 28. The trajectory
    # contract ramps from these (or from the live ramp value) - never sensors.
    return SetpointManager(
        database=FakeDatabase(
            [{"period_name": "Day", "heating_setpoint": 22.0, "cooling_setpoint": 28.0}]
        )
    )


@pytest.mark.asyncio
async def test_interrupted_ramp_resumes_from_live_value() -> None:
    # Given: a heating ramp from 22 to 20 over thirty minutes, fifteen minutes in.
    manager = _manager()
    started = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)
    now = started + timedelta(minutes=15)
    manager.ramp_manager.start_ramp("Veg Room", "main", "heating", 22.0, 20.0, 30.0, started)

    # When: a period change to Night (target 25) interrupts the in-flight ramp.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Night",
        {"heating_setpoint": 25.0, "ramp_in_duration": 30},
        previous_period="Day",
    )

    # Then: the replacement ramp continues from the live 21, not the Day table's 22.
    assert result["effective_heating_setpoint"] == pytest.approx(21.0)
    assert result["nominal_heating_setpoint"] == 25.0


@pytest.mark.asyncio
async def test_transition_anchor_is_the_scheduled_boundary() -> None:
    # Given: a period whose scheduled boundary is 10:01 Toronto time, observed by
    # the control loop four minutes late.
    manager = _manager()
    now = datetime(2026, 9, 20, 10, 5, tzinfo=LOCAL_TZ)

    # When: the transition to Night (target 20) ramps over thirty minutes.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Night",
        {
            "heating_setpoint": 20.0,
            "ramp_in_duration": 30,
            "period_start_time": "10:01",
        },
        previous_period="Day",
    )

    # Then: progress counts from the boundary, not the observation tick.
    expected = 22.0 + (20.0 - 22.0) * (4.0 / 30.0)
    assert result["effective_heating_setpoint"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_same_label_profile_change_retargets_with_original_deadline() -> None:
    # Given: an in-flight 20→24 ramp halfway at 22; both profiles label "Day".
    manager = _manager()
    identity_old = (2, 1, "Day", "06:00", "18:00")
    started = datetime(2026, 9, 20, 9, 0, tzinfo=LOCAL_TZ)
    now = started + timedelta(minutes=15)
    manager.ramp_manager.start_ramp("Veg Room", "main", "heating", 20.0, 24.0, 30.0, started)
    await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Day",
        {
            "heating_setpoint": 24.0,
            "ramp_in_duration": 30,
            "period_start_time": "09:00",
            "climate_identity": identity_old,
        },
    )

    # When: the same-label profile switches and the new target is 30.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Day",
        {
            "heating_setpoint": 30.0,
            "ramp_in_duration": 30,
            "period_start_time": "09:00",
            "climate_identity": (2, 2, "Day", "06:00", "18:00"),
        },
    )

    # Then: continuity is immediate (22) toward the new 30, and the original
    # scheduled deadline is retained instead of extending the ramp.
    assert result["effective_heating_setpoint"] == pytest.approx(22.0)
    assert result["nominal_heating_setpoint"] == 30.0
    ramp = manager.ramp_manager.active_ramps[("Veg Room", "main", "heating")]
    assert ramp.start_value == pytest.approx(22.0)
    assert ramp.target_value == pytest.approx(30.0)
    assert ramp.end_time == started + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_equal_target_noop_save_does_not_restart_ramp() -> None:
    # Given: an in-flight ramp toward the already configured nominal.
    manager = _manager()
    started = datetime(2026, 9, 20, 9, 0, tzinfo=LOCAL_TZ)
    now = started + timedelta(minutes=15)
    manager.ramp_manager.start_ramp("Veg Room", "main", "heating", 20.0, 24.0, 30.0, started)
    await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Day",
        {
            "heating_setpoint": 24.0,
            "ramp_in_duration": 30,
            "period_start_time": "09:00",
            "climate_identity": (2, 1, "Day", "06:00", "18:00"),
        },
    )
    ramp_before = manager.ramp_manager.active_ramps[("Veg Room", "main", "heating")]

    # When: a no-op save re-sends the same identity and target.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Day",
        {
            "heating_setpoint": 24.0,
            "ramp_in_duration": 30,
            "period_start_time": "09:00",
            "climate_identity": (2, 1, "Day", "06:00", "18:00"),
        },
    )

    # Then: the same ramp object continues; no restart happened.
    assert (
        manager.ramp_manager.active_ramps[("Veg Room", "main", "heating")] is ramp_before
    )
    assert result["effective_heating_setpoint"] == pytest.approx(22.0)


@pytest.mark.asyncio
async def test_outage_past_the_ramp_window_applies_nominal_directly() -> None:
    # Given: the service resumes forty-four minutes after a thirty-minute ramp
    # was scheduled to begin.
    manager = _manager()
    now = datetime(2026, 9, 20, 10, 45, tzinfo=LOCAL_TZ)

    # When: the transition is observed.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Night",
        {
            "heating_setpoint": 20.0,
            "ramp_in_duration": 30,
            "period_start_time": "10:01",
        },
        previous_period="Day",
    )

    # Then: the schedule already reached the target, so the setpoint applies
    # directly and no stale ramp stays active.
    assert result["effective_heating_setpoint"] == 20.0
    assert not manager.ramp_manager.has_active_ramps("Veg Room", "main")


@pytest.mark.asyncio
async def test_boundary_before_midnight_wraps_to_the_previous_day() -> None:
    # Given: a period that started at 23:00 while the loop now reads 01:00.
    manager = _manager()
    now = datetime(2026, 9, 21, 1, 0, tzinfo=LOCAL_TZ)

    # When: the transition ramps over thirty minutes.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        now,
        "Night",
        {
            "heating_setpoint": 20.0,
            "ramp_in_duration": 30,
            "period_start_time": "23:00",
        },
        previous_period="Day",
    )

    # Then: the two-hour-old boundary is past the ramp window: nominal applies.
    assert result["effective_heating_setpoint"] == 20.0
    assert not manager.ramp_manager.has_active_ramps("Veg Room", "main")


@pytest.mark.asyncio
async def test_restored_ramp_retargets_even_a_small_nominal_correction() -> None:
    manager = _manager()
    started = datetime(2026, 9, 20, 9, 0, tzinfo=LOCAL_TZ)
    now = started + timedelta(minutes=15)
    manager.ramp_manager.active_ramps[("Veg Room", "main", "heating")] = RampState(
        "heating", 20.0, 24.0, 30.0, started, restored=True
    )
    result = await manager.compute_effective_setpoints(
        "Veg Room", "main", now, "Day",
        {
            "heating_setpoint": 24.05,
            "ramp_in_duration": 30,
            "period_start_time": "09:00",
            "climate_identity": (1, None, "Day", "09:00", "18:00"),
        },
    )
    ramp = manager.ramp_manager.active_ramps[("Veg Room", "main", "heating")]
    assert result["effective_heating_setpoint"] == pytest.approx(22.0)
    assert ramp.target_value == 24.05
    assert ramp.end_time == started + timedelta(minutes=30)
