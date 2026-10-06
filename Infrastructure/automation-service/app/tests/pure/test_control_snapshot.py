from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any

from app.control.relay_board_state_manager import RelayBoardFreshness, RelayBoardSnapshot
from app.control.relay_manager import RelayControlState
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


class _Registry:
    def __init__(self, snapshot: RuntimeDeviceSnapshot) -> None:
        self.snapshot = snapshot


class _BoardState:
    def get_snapshot(self) -> RelayBoardSnapshot:
        observed_at = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
        return RelayBoardSnapshot((False,) * 16, observed_at, (observed_at,) * 16)

    def get_freshness(self) -> RelayBoardFreshness:
        return RelayBoardFreshness(status="FRESH", stale_since=None)


class _RelayManager:
    def get_channel_control_states(self) -> dict[int, RelayControlState]:
        return {0: RelayControlState(desired_state=1, mode="timed_on", syncing=True)}


class _DfrManager:
    def list_boards(self) -> list[dict[str, Any]]:
        return [{"board_id": 0, "available": True, "i2c_address": "hidden"}]

    def get_intensity(self, board_id: int, channel: int) -> float | None:
        return 42.0 if (board_id, channel) == (0, 0) else None


class _Alarms:
    def get_alarms(self) -> dict[str, dict[str, Any]]:
        return {
            "Veg Room:main:relay_mismatch_channel_0": {
                "location": "Veg Room",
                "cluster": "main",
                "alarm_name": "relay_mismatch_channel_0",
                "severity": "critical",
                "message": "Relay mismatch",
                "active": True,
            }
        }


def test_control_snapshot_contains_all_relays_dfr_slots_and_no_dfr_address() -> None:
    from app.control.control_snapshot_service import ControlSnapshotService

    # Given: one assigned relay and one initialized DFR board.
    snapshot = RuntimeDeviceSnapshot.create(
        version=4,
        hierarchy={
            "Veg Room": {
                "main": {
                    "heater_v_1": {
                        "device_id": 8,
                        "channel": 0,
                        "display_name": "Veg Heater",
                        "device_type": "heater",
                        "inherited_schedule_count": 2,
                        "inherited_schedule_summary": "Night heat",
                    }
                }
            }
        },
        mode_parameters={},
        active_modes={},
        light_intensities={},
        light_programs=[],
    )
    registry: Any = _Registry(snapshot)
    board_state: Any = _BoardState()
    relay_manager: Any = _RelayManager()
    dfr_manager: Any = _DfrManager()
    alarms: Any = _Alarms()
    service = ControlSnapshotService(registry, board_state, relay_manager, dfr_manager, alarms)

    # When: the composite control snapshot is assembled.
    response = service.get_snapshot()

    # Then: all physical slots are present with typed owner and command facts.
    assert response.registry_version == 4
    assert len(response.relays) == 16
    assert [(relay.physical_relay, relay.channel) for relay in response.relays] == [
        (physical_relay, channel)
        for physical_relay, channel in (
            (1, 15),
            (2, 0),
            (3, 14),
            (4, 1),
            (5, 13),
            (6, 2),
            (7, 12),
            (8, 3),
            (9, 11),
            (10, 4),
            (11, 10),
            (12, 5),
            (13, 9),
            (14, 6),
            (15, 8),
            (16, 7),
        )
    ]
    assert response.relays[1].assignment is not None
    assert response.relays[1].assignment.inherited_schedule_summary == "Night heat"
    assert response.relays[1].desired_state == 1
    assert response.relays[1].syncing is True
    assert len(response.dfr_boards) == 3
    assert [len(board.channels) for board in response.dfr_boards] == [2, 2, 2]
    assert response.dfr_boards[0].channels[0].commanded_intensity == 42.0
    assert "i2c_address" not in response.model_dump_json()


def test_relay_state_compatibility_endpoint_has_no_database_or_device_states_dependency() -> None:
    from app.routes.hardware import relay_state

    # Given: the narrow legacy endpoint and the relay-board owner.
    board_state = _BoardState()

    # When: its callable contract is inspected.
    parameter_names = relay_state.__code__.co_varnames[: relay_state.__code__.co_argcount]

    # Then: it cannot query database-backed device_states metadata.
    assert parameter_names == ("relay_board_state_manager",)
    assert board_state.get_snapshot().channels == (False,) * 16


def test_openapi_exporter_is_deterministic() -> None:
    # Given: two fresh destinations for the offline OpenAPI exporter.
    exporter = Path(__file__).parents[3] / "scripts" / "export_openapi.py"

    with TemporaryDirectory() as temporary_directory:
        first = Path(temporary_directory) / "first.json"
        second = Path(temporary_directory) / "second.json"

        # When: the schema is exported twice without starting the service.
        first_result = subprocess.run(
            [sys.executable, str(exporter), str(first)],
            check=False,
            capture_output=True,
            text=True,
        )
        second_result = subprocess.run(
            [sys.executable, str(exporter), str(second)],
            check=False,
            capture_output=True,
            text=True,
        )

        # Then: byte-for-byte output is stable across exports.
        assert first_result.returncode == 0, first_result.stderr
        assert second_result.returncode == 0, second_result.stderr
        assert first.read_bytes() == second.read_bytes()
