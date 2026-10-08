from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from monitoring_service.control_models import ControlHistoryRange
from monitoring_service.control_repository import ControlHistoryRepository
from monitoring_service.control_timeline_budget import ONE_DAC_CODE_PERCENT

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


def _light_row(
    timestamp: datetime,
    device_name: str,
    mode: str,
    value: float | None,
    ramp_progress: float | None = None,
) -> dict[str, str | float | datetime | None]:
    row = _setpoint_row(timestamp, 20.0, None)
    row.update(
        mode=mode,
        device_name=device_name,
        effective_light_intensity=value,
        nominal_light_intensity=value,
        ramp_progress_light=ramp_progress,
    )
    return row


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
        if "monitoring_photoperiod_coverage" in query:
            return [
                {
                    "id": 50,
                    "observed_at": NOW,
                    "location": "Veg Room",
                    "cluster": "main",
                    "state": "available",
                    "reason": "initial",
                    "runtime_snapshot_version": 2,
                }
            ]
        if "monitoring_room_photoperiod" in query:
            return [
                {
                    "id": 40 + index,
                    "observed_at": NOW + timedelta(minutes=index),
                    "location": "Veg Room",
                    "cluster": "main",
                    "phase": "SUN" if index < 2 else "MOON",
                    "mode_id": 3,
                    "submode_id": None,
                    "runtime_snapshot_version": 2,
                    "source": "photoperiod_transition",
                }
                for index in range(4)
            ]
        return []


@final
class LightTimelineDatabase:
    def __init__(self, rows: list[dict[str, str | float | datetime | None]]) -> None:
        self.rows = rows

    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | None]]:
        if "FROM effective_setpoints" in query:
            return self.rows
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
    assert any("monitoring_automation_state_5min" in query for query in database.queries)
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


@pytest.mark.anyio
async def test_budgeted_light_history_keeps_one_mode_aware_timeline_and_gap() -> None:
    # Given: a light switches modes, ramps, becomes unavailable, then turns off.
    timestamps = [NOW + timedelta(minutes=index) for index in range(6)]
    rows = [
        _light_row(timestamps[0], "light_f_1", "day", 40.0),
        _light_row(timestamps[1], "light_f_1", "night", 0.0),
        _light_row(timestamps[2], "light_f_1", "day", 80.0, 0.0),
        _light_row(timestamps[3], "light_f_1", "day", 100.0, 1.0),
        _light_row(timestamps[4], "light_f_1", "night", None),
        _light_row(timestamps[5], "light_f_1", "day", 0.0),
    ]
    repository = ControlHistoryRepository(LightTimelineDatabase(rows))

    # When: history is budgeted through the real repository path.
    response = await repository.read(
        "Flower Room",
        ControlHistoryRange(start=NOW, end=NOW + timedelta(minutes=6)),
        max_points=10,
    )

    # Then: mode transitions, ramp endpoints, and the explicit null gap survive once.
    assert [series.name for series in response.lights] == ["light_f_1"]
    light = response.lights[0]
    assert light.points == ()
    assert [(step.timestamp, step.value) for step in light.steps] == [
        (timestamps[0], 40.0),
        (timestamps[1], 0.0),
        (timestamps[4], None),
        (timestamps[5], 0.0),
    ]
    assert [(ramp.start, ramp.end, ramp.start_value, ramp.end_value) for ramp in light.linear] == [
        (timestamps[2], timestamps[3], 80.0, 100.0)
    ]


@final
class RawLightSetpointDatabase:
    """Serves raw per-device light rows to the dedicated light query only."""

    def __init__(self, rows: list[dict[str, str | float | datetime | None]]) -> None:
        self.rows = rows

    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | None]]:
        if "ramp_progress_light" in query:
            return self.rows
        return []


def _raw_light_row(
    timestamp: datetime,
    device_name: str,
    value: float | None,
    ramp_progress: float | None = None,
) -> dict[str, str | float | datetime | None]:
    return {
        "timestamp": timestamp,
        "mode": "SUN",
        "device_name": device_name,
        "effective_light_intensity": value,
        "nominal_light_intensity": 0.0 if value is None else value,
        "ramp_progress_light": ramp_progress,
    }


def _chain_value_at(
    linear: Sequence[tuple[datetime, datetime, float, float]], timestamp: datetime
) -> float | None:
    """Interpolate one recorded sample against the emitted ramp chain."""
    for start, end, start_value, end_value in linear:
        if start <= timestamp <= end:
            if end == start:
                return start_value
            fraction = (timestamp - start) / (end - start)
            return start_value + fraction * (end_value - start_value)
    return None


@pytest.mark.anyio
async def test_light_ramp_geometry_survives_every_supported_range_selection() -> None:
    # Given: one recorded ramp with held levels on both sides, one sample per minute.
    start = datetime(2026, 10, 5, 5, tzinfo=UTC)
    ramp_values = [
        round(14.9678 + index * 1.42 + ((index * 37) % 17) / 100.0, 6) for index in range(60)
    ]
    rows = (
        [
            _raw_light_row(start + timedelta(minutes=minute), "light_f_1", 0.0)
            for minute in (0, 1, 2)
        ]
        + [
            _raw_light_row(
                start + timedelta(minutes=3 + index),
                "light_f_1",
                ramp_values[index],
                ramp_progress=index / 59,
            )
            for index in range(60)
        ]
        + [
            _raw_light_row(start + timedelta(minutes=63 + minute), "light_f_1", ramp_values[-1])
            for minute in (0, 1)
        ]
    )
    repository = ControlHistoryRepository(RawLightSetpointDatabase(rows))

    # When: the same ramp is read through every budgeted range selection.
    chains = []
    for minutes in (70, 12 * 60, 24 * 60, 7 * 24 * 60):
        response = await repository.read(
            "Flower Room",
            ControlHistoryRange(start=start, end=start + timedelta(minutes=minutes)),
            max_points=1000,
        )
        light = response.lights[0]
        chains.append(
            [(ramp.start, ramp.end, ramp.start_value, ramp.end_value) for ramp in light.linear]
        )
        assert light.points == ()
        assert [(step.timestamp, step.value) for step in light.steps] == [
            (start, 0.0),
            (start + timedelta(minutes=63), ramp_values[-1]),
            (start + timedelta(minutes=65), None),
        ]

    # Then: every range reports the identical ramp chain anchored on the same samples.
    assert chains[0] == chains[1] == chains[2] == chains[3]
    chain = chains[0]
    assert (chain[0][0], chain[0][2]) == (start + timedelta(minutes=3), 14.9678)
    assert (chain[-1][1], chain[-1][3]) == (
        start + timedelta(minutes=62),
        ramp_values[-1],
    )
    tolerance = ONE_DAC_CODE_PERCENT + 1e-9
    for index, value in enumerate(ramp_values):
        emitted = _chain_value_at(chain, start + timedelta(minutes=3 + index))
        assert emitted is not None
        assert abs(emitted - value) <= tolerance


@pytest.mark.anyio
async def test_light_restart_dip_stays_visible_at_short_and_long_ranges() -> None:
    # Given: the real restart pattern: rising ramp, downward reset, resumed rise.
    start = datetime(2026, 10, 5, 21, tzinfo=UTC)
    values = [11.9, 12.4, 12.846687, 12.0, 12.049181, 12.6, 13.2, 13.8]
    rows = [
        _raw_light_row(
            start + timedelta(minutes=index),
            "light_f_1",
            value,
            ramp_progress=index / (len(values) - 1),
        )
        for index, value in enumerate(values)
    ]
    repository = ControlHistoryRepository(RawLightSetpointDatabase(rows))

    # When: the dip is read through a short and a long budgeted range.
    for minutes in (8, 7 * 24 * 60):
        response = await repository.read(
            "Flower Room",
            ControlHistoryRange(start=start, end=start + timedelta(minutes=minutes)),
            max_points=1000,
        )
        tuples = [
            (ramp.start, ramp.end, ramp.start_value, ramp.end_value)
            for ramp in response.lights[0].linear
        ]

        # Then: the reset and the resumed rise keep their exact sampled vertices.
        assert (
            start + timedelta(minutes=2),
            start + timedelta(minutes=3),
            12.846687,
            12.0,
        ) in tuples
        assert (
            start + timedelta(minutes=3),
            start + timedelta(minutes=4),
            12.0,
            12.049181,
        ) in tuples
        tolerance = ONE_DAC_CODE_PERCENT + 1e-9
        for index, value in enumerate(values):
            emitted = _chain_value_at(tuples, start + timedelta(minutes=index))
            assert emitted is not None
            assert abs(emitted - value) <= tolerance


@pytest.mark.anyio
async def test_light_budget_pressure_preserves_boundaries_and_reports_approximation() -> None:
    # Given: holds, a ramp whose every sample deviates beyond one DAC code, a gap, and OFF.
    start = NOW
    zigzag = [20.0 + (index % 2) * 4.0 for index in range(20)]
    rows = (
        [_raw_light_row(start + timedelta(minutes=minute), "light_f_1", 40.0) for minute in (0, 1)]
        + [
            _raw_light_row(
                start + timedelta(minutes=2 + index),
                "light_f_1",
                zigzag[index],
                ramp_progress=index / 19,
            )
            for index in range(20)
        ]
        + [
            _raw_light_row(start + timedelta(minutes=22), "light_f_1", 60.0),
            _raw_light_row(start + timedelta(minutes=23), "light_f_1", 60.0),
            _raw_light_row(start + timedelta(minutes=24), "light_f_1", None),
            _raw_light_row(start + timedelta(minutes=25), "light_f_1", 0.0),
        ]
    )
    repository = ControlHistoryRepository(RawLightSetpointDatabase(rows))

    # When: an impossibly small semantic budget is applied to the whole series.
    response = await repository.read(
        "Flower Room",
        ControlHistoryRange(start=start, end=start + timedelta(minutes=26)),
        max_points=10,
    )
    light = response.lights[0]
    tuples = [(ramp.start, ramp.end, ramp.start_value, ramp.end_value) for ramp in light.linear]

    # Then: mandatory boundaries stay exact and the ramp keeps its real deviations.
    assert light.points == ()
    assert [(step.timestamp, step.value) for step in light.steps] == [
        (start, 40.0),
        (start + timedelta(minutes=22), 60.0),
        (start + timedelta(minutes=24), None),
        (start + timedelta(minutes=25), 0.0),
    ]
    assert len(tuples) > 1
    tolerance = ONE_DAC_CODE_PERCENT + 1e-9
    for index, value in enumerate(zigzag):
        emitted = _chain_value_at(tuples, start + timedelta(minutes=2 + index))
        assert emitted is not None
        assert abs(emitted - value) <= tolerance
    assert len(light.steps) + 2 * len(light.linear) > 10
    assert light.provenance.is_aggregated is True


@pytest.mark.anyio
async def test_light_missing_rows_never_gain_fabricated_samples() -> None:
    # Given: samples around a silent outage followed by an OFF sample.
    start = NOW
    rows = [
        _raw_light_row(start, "light_f_1", 40.0),
        _raw_light_row(start + timedelta(minutes=1), "light_f_1", 40.0),
        _raw_light_row(start + timedelta(minutes=10), "light_f_1", 80.0),
        _raw_light_row(start + timedelta(minutes=11), "light_f_1", 0.0),
    ]
    repository = ControlHistoryRepository(RawLightSetpointDatabase(rows))

    # When: the outage window is read with a budget.
    response = await repository.read(
        "Flower Room",
        ControlHistoryRange(start=start, end=start + timedelta(minutes=12)),
        max_points=10,
    )

    # Then: observed values survive, but expired coverage cannot span the outage.
    light = response.lights[0]
    assert light.points == ()
    assert [(step.timestamp, step.value) for step in light.steps] == [
        (start, 40.0),
        (start + timedelta(minutes=2), None),
        (start + timedelta(minutes=10), 80.0),
        (start + timedelta(minutes=11), 0.0),
    ]
    assert light.linear == ()
