from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
import pytest
from relay_snapshot_fakes import Interlocks, Registry

from app.alarm_manager import AlarmManager
from app.control.device_command_service import (
    AutoCommand,
    DeviceCommandService,
    ManualOffCommand,
    TimedOnCommand,
)
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.events.operational_models import OperationalEvent
from app.routes.devices import control_device, set_device_mode
from app.schemas.device import DeviceControlRequest, DeviceModeRequest


class SamplingMcp:
    def __init__(self) -> None:
        self.samples = [(False,) * 16, (False,) * 16, (True,) + (False,) * 15]

    def sample_all_channels(self) -> tuple[bool, ...]:
        return self.samples.pop(0)


class EventSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class RelayMcp:
    def __init__(self) -> None:
        self.channels = (False,) * 16

    def sample_all_channels(self) -> tuple[bool, ...]:
        return self.channels

    def set_channel(self, channel: int, state: bool) -> bool:
        channels = list(self.channels)
        channels[channel] = state
        self.channels = tuple(channels)
        return True


class FailedRelayMcp(RelayMcp):
    def set_channel(self, _channel: int, _state: bool) -> bool:
        return False


class AlarmRecorder:
    def clear_alarm(self, *_args: str) -> bool:
        return True

    def raise_alarm(self, *_args: str) -> bool:
        return True


class CommandRelay:
    def __init__(self) -> None:
        self.state = 0
        self.succeeds = True

    def get_device_state(self, *_key: str) -> int:
        return self.state

    async def set_device_state(self, *_key: str) -> tuple[bool, None]:
        if not self.succeeds:
            return False, None
        self.state = int(_key[-2])
        return True, None

    def record_command_state(self, *_args: Any) -> None:
        return None


class ControlActions:
    async def log_control_action(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


class AlarmRedis:
    def write_alarm(self, *_args: str) -> bool:
        return True

    def clear_alarm(self, *_args: str) -> bool:
        return True

    def write_mode(self, *_args: str, **_kwargs: str) -> bool:
        return True

    def write_failsafe(self, *_args: str) -> bool:
        return True

    def clear_failsafe(self, *_args: str) -> bool:
        return True

    def read_alarms(self, *_args: str) -> dict[str, dict[str, bool | str]]:
        return {}


@pytest.mark.asyncio
async def test_board_emits_one_observation_event_for_a_physical_transition() -> None:
    sink = EventSink()
    board = RelayBoardStateManager(
        SamplingMcp(),
        event_sink=sink,
        now=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert await board.sample() is True
    assert await board.sample() is True
    assert await board.sample() is True

    assert [(event.event_type, event.payload.observed_state) for event in sink.events] == [
        ("relay.observed", True)
    ]


@pytest.mark.asyncio
async def test_matching_command_observation_emits_no_duplicate_relay_event() -> None:
    sink = EventSink()
    mcp = RelayMcp()
    board = RelayBoardStateManager(mcp, event_sink=sink)
    assert await board.sample() is True
    relay = RelayManager(mcp, Registry(), Interlocks(), board, event_sink=sink)

    assert await relay.set_device_state("Veg Room", "main", "heater", 1) == (True, None)
    assert await relay.set_device_state("Veg Room", "main", "heater", 1) == (True, None)

    assert [event.event_type for event in sink.events] == ["relay.commanded"]
    assert sink.events[0].correlation_id is not None


@pytest.mark.asyncio
async def test_mismatch_recovery_and_hardware_failure_emit_once_per_transition() -> None:
    sink = EventSink()
    mcp = RelayMcp()
    board = RelayBoardStateManager(mcp, event_sink=sink)
    assert await board.sample() is True
    relay = RelayManager(mcp, Registry(), Interlocks(), board, event_sink=sink)

    mcp.set_channel = lambda _channel, _state: True
    assert await relay.set_device_state("Veg Room", "main", "heater", 1) == (True, None)
    mcp.channels = (False, False, True) + (False,) * 13
    assert await board.sample() is True
    await relay.evaluate_observation(AlarmRecorder())

    failed_relay = RelayManager(FailedRelayMcp(), Registry(), Interlocks(), event_sink=sink)
    assert await failed_relay.set_device_state("Veg Room", "main", "heater", 1) == (
        False,
        "Hardware error",
    )
    assert await failed_relay.set_device_state("Veg Room", "main", "heater", 1) == (
        False,
        "Hardware error",
    )

    assert [event.event_type for event in sink.events] == [
        "relay.commanded",
        "relay.mismatch_detected",
        "relay.mismatch_recovered",
        "relay.command_failed",
    ]


@pytest.mark.asyncio
async def test_manual_lifecycle_emits_start_replace_extend_cancel_expire_and_auto_release() -> None:
    sink = EventSink()
    registry = Registry()
    clock = [datetime(2026, 9, 1, tzinfo=UTC)]
    command_service = DeviceCommandService(
        registry,
        CommandRelay(),
        ControlActions(),
        None,
        now=lambda: clock[0],
        event_sink=sink,
    )
    command_service.install_snapshot(registry.snapshot)

    await command_service.execute("Veg Room", "main", "heater", ManualOffCommand())
    await command_service.execute("Veg Room", "main", "heater", TimedOnCommand(duration_seconds=30))
    await command_service.execute("Veg Room", "main", "heater", TimedOnCommand(duration_seconds=60))
    await command_service.execute("Veg Room", "main", "heater", AutoCommand())
    await command_service.execute("Veg Room", "main", "heater", TimedOnCommand(duration_seconds=30))
    clock[0] += timedelta(seconds=31)
    await command_service.expire_commands()
    await command_service.execute("Veg Room", "main", "heater", ManualOffCommand())
    await command_service.execute("Veg Room", "main", "heater", AutoCommand())
    await command_service.execute("Veg Room", "main", "heater", AutoCommand())

    assert [event.event_type for event in sink.events] == [
        "manual_override.started",
        "manual_override.replaced",
        "manual_override.extended",
        "manual_override.cancelled",
        "manual_override.started",
        "manual_override.expired",
        "manual_override.started",
        "manual_override.released",
    ]


@pytest.mark.asyncio
async def test_legacy_device_routes_reuse_manual_events_and_suppress_noop_and_failure() -> None:
    # Given: legacy routes sharing the authoritative command service and its semantic event sink.
    sink = EventSink()
    registry = Registry()
    relay = CommandRelay()
    command_service = DeviceCommandService(registry, relay, ControlActions(), None, event_sink=sink)
    command_service.install_snapshot(registry.snapshot)

    # When: the mode alias is a no-op, both aliases succeed, then a command fails before audit.
    await set_device_mode(
        "Veg Room", "main", "heater", DeviceModeRequest(mode="auto"), command_service
    )
    await set_device_mode(
        "Veg Room", "main", "heater", DeviceModeRequest(mode="manual"), command_service
    )
    await control_device(
        "Veg Room",
        "main",
        "heater",
        DeviceControlRequest(state=1, duration_seconds=30),
        command_service,
    )
    relay.succeeds = False
    with pytest.raises(HTTPException):
        await control_device(
            "Veg Room", "main", "heater", DeviceControlRequest(state=0), command_service
        )

    # Then: only the Todo 6 semantic lifecycle producer records successful changed commands.
    assert [event.event_type for event in sink.events] == [
        "manual_override.started",
        "manual_override.replaced",
    ]


def test_critical_alarm_emits_a_failsafe_transition_once_and_clear_emits_recovery() -> None:
    sink = EventSink()
    alarms = AlarmManager(AlarmRedis(), event_sink=sink)

    assert alarms.raise_alarm("Veg Room", "main", "relay_mismatch", "critical", "Mismatch") is True
    assert alarms.raise_alarm("Veg Room", "main", "relay_mismatch", "critical", "Mismatch") is True
    assert alarms.clear_failsafe("Veg Room", "main") is True
    assert alarms.clear_failsafe("Veg Room", "main") is True

    assert [event.event_type for event in sink.events] == [
        "system.failsafe_triggered",
        "system.failsafe_cleared",
    ]
