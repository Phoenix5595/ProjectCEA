"""On-demand composite projection of the independent control-state owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final, final

from app.alarm_manager import AlarmManager
from app.control.device_command_service import DeviceCommandService
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayControlState, RelayManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import DeviceKey, RuntimeDeviceSnapshot
from app.hardware.dfr0971 import DFR0971Manager
from app.schemas.control_snapshot import (
    ControlSnapshotResponse,
    DeviceAssignmentResponse,
    DfrBoardSnapshotResponse,
    DfrChannelSnapshotResponse,
    HardwareAlarmResponse,
    RelayControlSnapshotResponse,
)
from shared.relay_topology import RELAY_TOPOLOGY

_DFR_BOARD_IDS: Final[tuple[int, int, int]] = (0, 1, 2)
_DFR_CHANNELS: Final[tuple[int, int]] = (0, 1)


@final
class ControlSnapshotService:
    """Assemble one read model without creating another runtime state owner."""

    def __init__(
        self,
        runtime_device_registry: RuntimeDeviceRegistry,
        relay_board_state_manager: RelayBoardStateManager,
        relay_manager: RelayManager,
        dfr0971_manager: DFR0971Manager | None,
        alarm_manager: AlarmManager | None,
        device_command_service: DeviceCommandService | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runtime_device_registry = runtime_device_registry
        self._relay_board_state_manager = relay_board_state_manager
        self._relay_manager = relay_manager
        self._dfr0971_manager = dfr0971_manager
        self._alarm_manager = alarm_manager
        self._device_command_service = device_command_service
        self._now = now

    def get_snapshot(self) -> ControlSnapshotResponse:
        """Return a projection rebuilt once if registry publication races assembly."""
        first_registry, first_response = self._capture()
        if self._runtime_device_registry.snapshot is first_registry:
            return first_response
        _second_registry, second_response = self._capture()
        return second_response

    def _capture(self) -> tuple[RuntimeDeviceSnapshot, ControlSnapshotResponse]:
        registry_snapshot = self._runtime_device_registry.snapshot
        board_snapshot = self._relay_board_state_manager.get_snapshot()
        freshness = self._relay_board_state_manager.get_freshness()
        control_states = self._relay_manager.get_channel_control_states()
        alarms = self._hardware_alarms()
        assignments = self._assignments(registry_snapshot)
        dfr_availability = self._dfr_availability()
        response = ControlSnapshotResponse(
            generated_at=self._now(),
            registry_version=registry_snapshot.version,
            sampled_at=board_snapshot.sampled_at,
            freshness=freshness.status,
            stale_since=freshness.stale_since,
            relays=tuple(
                self._relay_entry(
                    entry.physical_relay,
                    entry.channel,
                    entry.pin_label,
                    board_snapshot.channels,
                    board_snapshot.changed_at,
                    freshness.status == "STALE",
                    registry_snapshot,
                    assignments,
                    control_states,
                    alarms,
                )
                for entry in RELAY_TOPOLOGY
            ),
            dfr_boards=tuple(
                self._dfr_board(board_id, dfr_availability, registry_snapshot, assignments)
                for board_id in _DFR_BOARD_IDS
            ),
            hardware_alarms=tuple(sorted(alarms.values(), key=lambda alarm: alarm.alarm_name)),
        )
        return registry_snapshot, response

    def _relay_entry(
        self,
        physical_relay: int,
        channel: int,
        pin_label: str,
        observed_channels: tuple[bool, ...] | None,
        changed_at: tuple[datetime | None, ...],
        stale: bool,
        registry_snapshot: RuntimeDeviceSnapshot,
        assignments: Mapping[DeviceKey, DeviceAssignmentResponse],
        control_states: Mapping[int, RelayControlState],
        alarms: Mapping[str, HardwareAlarmResponse],
    ) -> RelayControlSnapshotResponse:
        key = registry_snapshot.by_channel.get(channel)
        assignment = assignments.get(key) if key is not None else None
        state = control_states.get(channel)
        command = (
            self._device_command_service.get_command_state(*key)
            if key is not None and self._device_command_service is not None
            else None
        )
        alarm_name = (
            f"relay_mismatch_channel_{channel}"
            if key is not None
            else f"unassigned_relay_mismatch_channel_{channel}"
        )
        return RelayControlSnapshotResponse(
            physical_relay=physical_relay,
            channel=channel,
            pin_label=pin_label,
            observed_state=observed_channels[channel] if observed_channels is not None else None,
            changed_at=changed_at[channel],
            assignment=assignment,
            desired_state=state.desired_state if state is not None else None,
            command_mode=command.mode
            if command is not None
            else state.mode
            if state is not None
            else None,
            command_expires_at=(
                command.expires_at
                if command is not None
                else state.expires_at
                if state is not None
                else None
            ),
            prior_command_mode=(
                command.prior_mode
                if command is not None
                else state.prior_mode
                if state is not None
                else None
            ),
            syncing=state.syncing if state is not None else False,
            stale=stale,
            last_command_succeeded=state.last_command_succeeded if state is not None else None,
            recovery_pending=state.recovery_pending if state is not None else False,
            alarm=alarms.get(alarm_name),
        )

    @staticmethod
    def _assignments(
        snapshot: RuntimeDeviceSnapshot,
    ) -> dict[DeviceKey, DeviceAssignmentResponse]:
        return {
            key: DeviceAssignmentResponse(
                device_id=device_id,
                location=key[0],
                cluster=key[1],
                device_name=key[2],
                display_name=_string(snapshot.device_info[key].get("display_name")),
                device_type=_string(snapshot.device_info[key].get("device_type")),
                inherited_schedule_count=_integer(
                    snapshot.device_info[key].get("inherited_schedule_count")
                ),
                inherited_schedule_summary=_string(
                    snapshot.device_info[key].get("inherited_schedule_summary")
                ),
            )
            for key, device_id in snapshot.by_device.items()
        }

    def _dfr_availability(self) -> dict[int, bool]:
        if self._dfr0971_manager is None:
            return {}
        return {
            board_id: bool(board.get("available"))
            for board in self._dfr0971_manager.list_boards()
            if isinstance(board.get("board_id"), int)
            for board_id in (board["board_id"],)
        }

    def _dfr_board(
        self,
        board_id: int,
        availability: Mapping[int, bool],
        registry_snapshot: RuntimeDeviceSnapshot,
        assignments: Mapping[DeviceKey, DeviceAssignmentResponse],
    ) -> DfrBoardSnapshotResponse:
        available = availability.get(board_id, False)
        return DfrBoardSnapshotResponse(
            board_id=board_id,
            available=available,
            channels=tuple(
                self._dfr_channel(board_id, channel, available, registry_snapshot, assignments)
                for channel in _DFR_CHANNELS
            ),
        )

    def _dfr_channel(
        self,
        board_id: int,
        channel: int,
        available: bool,
        registry_snapshot: RuntimeDeviceSnapshot,
        assignments: Mapping[DeviceKey, DeviceAssignmentResponse],
    ) -> DfrChannelSnapshotResponse:
        commanded_intensity = (
            self._dfr0971_manager.get_intensity(board_id, channel)
            if available and self._dfr0971_manager is not None
            else None
        )
        assignment = next(
            (
                assignment
                for key, assignment in assignments.items()
                if self._dfr_slot_for_key(registry_snapshot, key) == (board_id, channel)
            ),
            None,
        )
        return DfrChannelSnapshotResponse(
            channel=channel,
            available=available,
            assignment=assignment,
            commanded_intensity=commanded_intensity,
            command_acknowledged=commanded_intensity is not None,
        )

    @staticmethod
    def _dfr_slot_for_key(
        registry_snapshot: RuntimeDeviceSnapshot, key: DeviceKey
    ) -> tuple[int, int] | None:
        info = registry_snapshot.device_info[key]
        board_id = info.get("dimming_board_id")
        channel = info.get("dimming_channel")
        if isinstance(board_id, int) and isinstance(channel, int):
            return board_id, channel
        return None

    def _hardware_alarms(self) -> dict[str, HardwareAlarmResponse]:
        if self._alarm_manager is None:
            return {}
        return {
            alarm_name: HardwareAlarmResponse(
                location=_string_or_empty(alarm.get("location")),
                cluster=_string_or_empty(alarm.get("cluster")),
                alarm_name=_string_or_empty(alarm.get("alarm_name")),
                severity=_string_or_empty(alarm.get("severity")),
                message=_string_or_empty(alarm.get("message")),
            )
            for alarm_name, alarm in self._alarm_manager.get_alarms().items()
            if alarm.get("active") is True
        }


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0
