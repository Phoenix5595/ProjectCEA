"""Alignment contract between the runtime ramp engine and the schedule trajectory.

The chart's trajectory is the contract: ramps resume from the live interpolated
value when a period change interrupts them, and their anchor is the scheduled
period boundary rather than the tick that observed the change.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import pytest

from app.control.setpoint_manager import SetpointManager
from app.control.scheduler import LOCAL_TZ


class FakePeriodsRepo:
    def __init__(self, periods: list[dict[str, Any]]) -> None:
        self.periods = periods
        self.calls: list[tuple[str, str, int | None, int | None]] = []

    async def get_periods(
        self,
        location: str,
        cluster: str,
        mode_id: int | None = None,
        submode_id: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((location, cluster, mode_id, submode_id))
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


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit_nowait(self, event: Any) -> None:
        self.events.append(event)


def _period(
    name: str,
    start: time,
    end: time,
    heating: float,
    ramp: int,
    *,
    cooling: float | None = None,
    vpd: float | None = None,
) -> dict[str, Any]:
    return {
        "period_name": name,
        "start_time": start,
        "end_time": end,
        "ramp_minutes": ramp,
        "heating_setpoint": heating,
        "cooling_setpoint": cooling,
        "vpd_setpoint": vpd,
        "co2_setpoint": None,
    }


FLOWER_PROFILE = [
    _period("Jour", time(0, 0), time(10, 0), 25.0, 0, cooling=27.0, vpd=1.1),
    _period("Nuit", time(10, 0), time(16, 0), 24.0, 60, cooling=27.0, vpd=1.1),
    _period("Jour2", time(16, 0), time(0, 0), 25.0, 60, cooling=27.0, vpd=1.1),
]

NUIT_DATA: dict[str, Any] = {
    "heating_setpoint": 24.0,
    "cooling_setpoint": 27.0,
    "vpd": 1.1,
    "ramp_in_duration": 60,
    "period_start_time": "10:00",
    "climate_identity": (1, None, "Nuit", "10:00", "16:00"),
}


def _fresh_manager(
    rows: list[dict[str, Any]],
) -> tuple[SetpointManager, FakeDatabase, RecordingSink]:
    database = FakeDatabase(rows)
    sink = RecordingSink()
    return SetpointManager(database=database, event_sink=sink), database, sink


@pytest.mark.asyncio
async def test_first_evaluation_inside_the_ramp_window_resumes_the_scheduled_ramp() -> None:
    # Given: a process that starts 16.5 min into the 10:00 Nuit ramp (25 -> 24 over 60 min).
    manager, database, sink = _fresh_manager(FLOWER_PROFILE)
    now = datetime(2026, 10, 25, 10, 16, 30, tzinfo=LOCAL_TZ)

    # When: the room is evaluated for the first time, with no previous period known.
    result = await manager.compute_effective_setpoints(
        "Flower Room", "main", now, "Nuit", NUIT_DATA
    )

    # Then: the effective value sits on the scheduled line from the predecessor's target.
    assert result["effective_heating_setpoint"] == pytest.approx(24.725)
    assert result["ramp_progress_heating"] == pytest.approx(0.275)
    assert result["effective_cooling_setpoint"] == 27.0
    assert result["effective_vpd_setpoint"] == 1.1
    ramp = manager.ramp_manager.active_ramps[("Flower Room", "main", "heating")]
    assert ramp.start_time == datetime(2026, 10, 25, 10, 0, tzinfo=LOCAL_TZ)
    assert ramp.end_time == datetime(2026, 10, 25, 11, 0, tzinfo=LOCAL_TZ)
    assert sink.events == []
    assert database.climate_periods_repo.calls == [("Flower Room", "main", 1, None)]


@pytest.mark.asyncio
async def test_first_evaluation_after_the_ramp_window_holds_nominal() -> None:
    # Given: a process that starts after the Nuit ramp window closed at 11:00.
    manager, _database, _sink = _fresh_manager(FLOWER_PROFILE)

    # When: the room is evaluated for the first time.
    result = await manager.compute_effective_setpoints(
        "Flower Room",
        "main",
        datetime(2026, 10, 25, 11, 5, tzinfo=LOCAL_TZ),
        "Nuit",
        NUIT_DATA,
    )

    # Then: the nominal target holds and no ramp is resumed.
    assert result["effective_heating_setpoint"] == 24.0
    assert result["ramp_progress_heating"] is None
    assert not manager.ramp_manager.has_active_ramps("Flower Room", "main")


@pytest.mark.asyncio
async def test_first_period_of_the_day_resumes_from_the_last_period() -> None:
    # Given: P1 starts the day at 00:00, so its predecessor wraps to P3 (heating 22).
    manager, _database, _sink = _fresh_manager(
        [
            _period("P1", time(0, 0), time(9, 0), 25.0, 30),
            _period("P2", time(9, 0), time(16, 0), 23.0, 60),
            _period("P3", time(16, 0), time(0, 0), 22.0, 30),
        ]
    )

    # When: the process starts halfway through P1's 30-minute ramp.
    result = await manager.compute_effective_setpoints(
        "Veg Room",
        "main",
        datetime(2026, 10, 26, 0, 15, tzinfo=LOCAL_TZ),
        "P1",
        {
            "heating_setpoint": 25.0,
            "ramp_in_duration": 30,
            "period_start_time": "00:00",
            "climate_identity": (1, None, "P1", "00:00", "09:00"),
        },
    )

    # Then: heating is halfway from P3's 22 to P1's 25.
    assert result["effective_heating_setpoint"] == pytest.approx(23.5)
