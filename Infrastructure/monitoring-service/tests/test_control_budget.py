from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from monitoring_service.control_models import ControlHistoryRange
from monitoring_service.control_repository import ControlHistoryRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _setpoint_row(
    timestamp: datetime, heating: float, ramp_progress: float | None
) -> dict[str, str | float | datetime | None]:
    return {
        "timestamp": timestamp,
        "mode": "day",
        "effective_heating_setpoint": heating,
        "nominal_heating_setpoint": heating,
        "ramp_progress_heating": ramp_progress,
        "effective_cooling_setpoint": None,
        "nominal_cooling_setpoint": None,
        "ramp_progress_cooling": None,
        "effective_humidity_setpoint": None,
        "nominal_humidity_setpoint": None,
        "ramp_progress_humidity": None,
        "effective_co2_setpoint": None,
        "nominal_co2_setpoint": None,
        "ramp_progress_co2": None,
        "effective_vpd_setpoint": None,
        "nominal_vpd_setpoint": None,
        "ramp_progress_vpd": None,
        "device_name": None,
        "effective_light_intensity": None,
        "nominal_light_intensity": None,
        "ramp_progress_light": None,
    }


@final
class DenseControlDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | int | None]]:
        if "FROM effective_setpoints" in query:
            rows = [
                _setpoint_row(NOW + timedelta(minutes=index), 20.0, None)
                if index < 2
                else _setpoint_row(
                    NOW + timedelta(minutes=index),
                    20.0 + (index - 2),
                    (index - 2) / 5,
                )
                if index < 8
                else _setpoint_row(NOW + timedelta(minutes=index), 25.0, None)
                for index in range(12)
            ]
            for index, row in enumerate(rows):
                row["device_name"] = "light_v_1"
                row["effective_light_intensity"] = float(index * 10)
                row["nominal_light_intensity"] = float(index * 10)
                row["ramp_progress_light"] = index / 11
            return rows
        if "monitoring_automation_state" in query:
            return [
                {
                    "bucket": NOW + timedelta(minutes=index),
                    "device_name": "exhaust_f_1",
                    "device_state_last": 0 if 4 <= index < 8 else 1,
                    "device_mode_last": "auto",
                    "control_reason_last": "idle" if 4 <= index < 8 else "schedule",
                    "pid_output_last": 12.5,
                    "duty_cycle_percent_last": 30.0,
                }
                for index in range(12)
            ]
        if "monitoring_room_photoperiod" in query:
            return [
                {
                    "observed_at": NOW + timedelta(minutes=index),
                    "phase": "SUN" if index < 2 else "MOON",
                    "mode_id": 3,
                    "submode_id": None,
                    "runtime_snapshot_version": 2,
                }
                for index in range(4)
            ]
        return []


@final
class AggregateControlDatabase:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | int | None]]:
        self.queries.append(query)
        if "monitoring_effective_setpoints_5min" in query:
            return [_setpoint_row(NOW, 21.0, None)]
        if "monitoring_automation_state_5min" in query:
            return [
                {
                    "bucket": NOW,
                    "device_name": "heater_v_1",
                    "device_state_last": 1,
                    "device_mode_last": "auto",
                    "control_reason_last": "schedule",
                    "pid_output_last": 15.0,
                    "duty_cycle_percent_last": 25.0,
                }
            ]
        return []


@final
class GapControlDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | int | None]]:
        if "FROM effective_setpoints" in query:
            unavailable = _setpoint_row(NOW + timedelta(minutes=1), 20.0, None)
            unavailable["effective_heating_setpoint"] = None
            return [
                _setpoint_row(NOW, 20.0, None),
                unavailable,
                _setpoint_row(NOW + timedelta(minutes=2), 22.0, None),
            ]
        return []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_budgeted_history_preserves_step_holds_ramp_endpoints_and_phase_transitions() -> None:
    # Given: dense repeated states, a linear climate ramp, and duplicate phase observations.
    history_range = ControlHistoryRange(start=NOW, end=NOW + timedelta(minutes=12))
    repository = ControlHistoryRepository(DenseControlDatabase())

    # When: a client supplies the minimum legal semantic-series budget.
    response = await repository.read("Veg Room", history_range, max_points=10)

    # Then: repeated values collapse, categorical facts remain exact, and the ramp is explicit.
    climate = response.climate[0]
    assert response.requested_max_points == 10
    assert response.interval_seconds == 120
    assert len(climate.points) + len(climate.steps) + (2 * len(climate.linear)) <= 10
    assert [(step.timestamp, step.value) for step in climate.steps] == [
        (NOW, 20.0),
        (NOW + timedelta(minutes=8), 25.0),
    ]
    assert [
        (linear.start, linear.end, linear.start_value, linear.end_value)
        for linear in climate.linear
    ] == [(NOW + timedelta(minutes=2), NOW + timedelta(minutes=7), 20.0, 25.0)]
    light = response.lights[0]
    assert len(light.points) + len(light.steps) + (2 * len(light.linear)) <= 10
    assert [(linear.start_value, linear.end_value) for linear in light.linear] == [(0.0, 110.0)]
    device_points = response.devices[0].points
    assert [
        (point.device_state, point.device_mode, point.control_reason) for point in device_points
    ] == [
        (1.0, "auto", "schedule"),
        (0.0, "auto", "idle"),
        (1.0, "auto", "schedule"),
    ]
    assert len(device_points) <= 10
    assert len(response.pid[0].points) == 1
    assert [point.phase for point in response.photoperiod] == ["SUN", "MOON"]


@pytest.mark.anyio
async def test_budgeted_history_uses_aggregated_last_value_sources_for_long_windows() -> None:
    # Given: a long request served by the existing five-minute CAGG read models.
    database = AggregateControlDatabase()
    repository = ControlHistoryRepository(database)
    history_range = ControlHistoryRange(start=NOW - timedelta(days=2), end=NOW)

    # When: the caller applies a point budget.
    response = await repository.read("Veg Room", history_range, max_points=10)

    # Then: the last-value rows and their aggregated provenance survive the budget path.
    assert "monitoring_effective_setpoints_5min" in database.queries[0]
    assert "monitoring_automation_state_5min" in database.queries[1]
    assert response.climate[0].provenance.is_aggregated is True
    assert response.climate[0].steps[0].value == 21.0
    assert response.devices[0].provenance.is_aggregated is True


@pytest.mark.anyio
async def test_budgeted_history_keeps_unavailable_setpoint_gaps_unbridged() -> None:
    # Given: a raw setpoint series with one unavailable observation between valid values.
    repository = ControlHistoryRepository(GapControlDatabase())
    history_range = ControlHistoryRange(start=NOW, end=NOW + timedelta(minutes=3))

    # When: semantic thinning converts held values to steps.
    response = await repository.read("Veg Room", history_range, max_points=10)

    # Then: the gap becomes an unavailable null step rather than a held-value bridge.
    steps = response.climate[0].steps
    assert [(step.timestamp, step.value) for step in steps] == [
        (NOW, 20.0),
        (NOW + timedelta(minutes=1), None),
        (NOW + timedelta(minutes=2), 22.0),
    ]
    assert steps[1].provenance.quality == "unavailable"
