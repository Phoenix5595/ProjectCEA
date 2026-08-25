from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from monitoring_service.control_models import ControlHistoryRange
from monitoring_service.control_repository import ControlHistoryRepository

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@final
class CharacterizationDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        if "FROM effective_setpoints" in query:
            return [
                {
                    "timestamp": NOW - timedelta(seconds=5),
                    "mode": "day",
                    "effective_heating_setpoint": 22.0,
                    "nominal_heating_setpoint": 21.0,
                    "ramp_progress_heating": None,
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
            ]
        if "monitoring_automation_state_1min" in query:
            return [
                {
                    "bucket": NOW - timedelta(seconds=10),
                    "device_name": "light_f_1",
                    "device_state_last": 1,
                    "device_mode_last": "auto",
                    "control_reason_last": "schedule",
                    "pid_output_last": None,
                    "duty_cycle_percent_last": 40.0,
                }
            ]
        if "monitoring_room_photoperiod" in query:
            return [
                {
                    "observed_at": NOW - timedelta(minutes=30),
                    "phase": "SUN",
                    "mode_id": 3,
                    "submode_id": None,
                    "runtime_snapshot_version": 2,
                }
            ]
        return []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_history_without_budget_matches_characterized_legacy_envelope() -> None:
    # Given: legacy raw and aggregate read-model facts from a deterministic fake database.
    history_range = ControlHistoryRange(start=NOW - timedelta(minutes=5), end=NOW)

    # When: callers omit the point budget.
    response = await ControlHistoryRepository(CharacterizationDatabase()).read(
        "Veg Room", history_range
    )

    # Then: the serialized envelope remains byte-for-byte identical to the captured legacy output.
    assert response.model_dump_json() == (
        '{"range":{"start":"2026-08-20T11:55:00Z","end":"2026-08-20T12:00:00Z"},'
        '"runtime_snapshot_version":2,"requested_max_points":null,"interval_seconds":null,'
        '"cursors":[],"flush_health":[],"climate":[{"name":"heating_setpoint",'
        '"provenance":{"origin":"recorded","quality":"exact","is_aggregated":false},'
        '"warnings":[],"points":[{"timestamp":"2026-08-20T11:59:55Z","value":22.0,'
        '"provenance":{"origin":"recorded","quality":"exact","is_aggregated":false},'
        '"metric":"heating_setpoint","nominal_value":21.0,"ramp_progress":null,'
        '"mode":"day","device_name":null}],"steps":[],"linear":[]}],"lights":[],'
        '"devices":[{"name":"light_f_1","provenance":{"origin":"recorded",'
        '"quality":"exact","is_aggregated":true},"warnings":[],"points":['
        '{"timestamp":"2026-08-20T11:59:50Z","provenance":{"origin":"recorded",'
        '"quality":"exact","is_aggregated":true},"device_name":"light_f_1",'
        '"device_state":1.0,"device_mode":"auto","control_reason":"schedule"}]}],'
        '"pid":[{"name":"light_f_1","provenance":{"origin":"recorded",'
        '"quality":"exact","is_aggregated":true},"warnings":[],"points":['
        '{"timestamp":"2026-08-20T11:59:50Z","provenance":{"origin":"recorded",'
        '"quality":"exact","is_aggregated":true},"device_name":"light_f_1",'
        '"pid_output":null,"duty_cycle_percent":40.0}]}],"photoperiod":['
        '{"timestamp":"2026-08-20T11:30:00Z","phase":"SUN","provenance":'
        '{"origin":"recorded","quality":"exact","is_aggregated":true},"mode_id":3,'
        '"submode_id":null,"runtime_snapshot_version":2}]}'
    )
