from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.control.control_snapshot_service import ControlSnapshotService
from app.control.device_command_service import (
    AutoCommand,
    DeviceCommandService,
    ManualOffCommand,
    TimedOnCommand,
)
from app.control.device_controller import DeviceController
from app.control.device_processor import DeviceProcessor
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


class _Clock:
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)

    def __call__(self):
        return self.now


class _Registry:
    def __init__(self):
        bindings = {
            "light_f_1": (2, 3, 2, 0),
            "light_f_2": (3, 2, 1, 1),
            "light_f_3": (4, 12, 2, 1),
        }
        self.snapshot = RuntimeDeviceSnapshot.create(
            version=1,
            hierarchy={
                "Flower Room": {
                    "main": {
                        name: {
                            "device_id": device_id,
                            "channel": relay,
                            "device_type": "light",
                            "display_name": name,
                            "dimming_enabled": True,
                            "dimming_type": "dfr0971",
                            "dimming_board_id": board,
                            "dimming_channel": channel,
                        }
                        for name, (device_id, relay, board, channel) in bindings.items()
                    }
                }
            },
            mode_parameters={},
            active_modes={},
            light_intensities={},
            light_programs=[],
        )

    def subscribe(self, consumer):
        consumer(self.snapshot)


class _Mcp:
    def __init__(self):
        self.channels = [False] * 16

    def set_channel(self, channel, state):
        self.channels[channel] = state
        return True

    def sample_all_channels(self):
        return tuple(self.channels)


class _Dimmer:
    def __init__(self):
        self.values = {(2, 0): 0, (1, 1): 0, (2, 1): 0}

    def set_intensity(self, board, channel, intensity):
        self.values[(board, channel)] = intensity
        return True

    def get_intensity(self, board, channel):
        return self.values.get((board, channel))

    def list_boards(self):
        return [{"board_id": board, "available": True} for board in range(3)]


class _History:
    async def log_control_action(self, *args, **kwargs):
        return True


class _Redis:
    redis_client = None
    redis_enabled = True
    failsafe = False

    def read_failsafe(self, location, cluster):
        return {} if self.failsafe else None

    def write_light_intensity(self, *args):
        pass


def _rig():
    clock = _Clock()
    registry: Any = _Registry()
    mcp: Any = _Mcp()
    dimmer: Any = _Dimmer()
    history: Any = _History()
    redis: Any = _Redis()
    board = RelayBoardStateManager(mcp, now=clock)
    interlocks: Any = SimpleNamespace(check_interlock=lambda *args, **kwargs: (True, None))
    relay = RelayManager(mcp, registry, interlocks, relay_board_state_manager=board, now=clock)
    commands = DeviceCommandService(registry, relay, history, redis, now=clock)
    policy: Any = SimpleNamespace(observe=lambda *args: None, emit_lifecycle=lambda *args: None)
    database: Any = SimpleNamespace(control_action_repo=history, _automation_redis=redis)
    controller = DeviceController(relay, database, policy, dimmer)
    processor = DeviceProcessor(controller, database, dimmer, device_command_service=commands)
    snapshot = ControlSnapshotService(registry, board, relay, dimmer, None, commands, now=clock)
    return SimpleNamespace(
        clock=clock,
        registry=registry,
        board=board,
        dimmer=dimmer,
        commands=commands,
        processor=processor,
        snapshot=snapshot,
        redis=redis,
    )


async def _tick(rig):
    await rig.board.sample()
    await rig.commands.expire_commands()
    await rig.processor.process_devices(
        "Flower Room",
        "main",
        rig.registry.snapshot.hierarchy["Flower Room"]["main"],
        {},
        rig.clock.now,
        None,
        "Constant",
        is_sun=False,
    )


def _flower_relays(rig):
    return [row for row in rig.snapshot.get_snapshot().relays if row.assignment is not None]


@pytest.mark.asyncio
async def test_five_minute_light_override_survives_moon_ticks_and_expires_to_off():
    rig = _rig()
    await rig.commands.initialize_startup()
    await _tick(rig)
    for name in rig.registry.snapshot.hierarchy["Flower Room"]["main"]:
        await rig.commands.execute(
            "Flower Room", "main", name, TimedOnCommand(duration_seconds=300)
        )

    await _tick(rig)
    assert rig.dimmer.values == {(2, 0): 10, (1, 1): 10, (2, 1): 10}
    assert all(
        row.observed_state is True and row.desired_state == 1 and row.command_mode == "timed_on"
        for row in _flower_relays(rig)
    )
    assert {row.command_expires_at for row in _flower_relays(rig)} == {
        rig.clock.now + timedelta(seconds=300)
    }

    rig.clock.now += timedelta(seconds=299)
    await _tick(rig)
    assert all(row.observed_state is True for row in _flower_relays(rig))
    assert rig.dimmer.values == {(2, 0): 10, (1, 1): 10, (2, 1): 10}

    rig.clock.now += timedelta(seconds=1)
    await _tick(rig)
    assert all(
        row.observed_state is False
        and row.command_mode == "auto"
        and row.command_expires_at is None
        for row in _flower_relays(rig)
    )
    assert rig.dimmer.values == {(2, 0): 0, (1, 1): 0, (2, 1): 0}


@pytest.mark.asyncio
async def test_timed_command_applies_ten_percent_with_legacy_manual_light_metadata():
    rig = _rig()
    await rig.commands.initialize_startup()
    await rig.board.sample()
    name = "light_f_1"
    await rig.commands.execute("Flower Room", "main", name, TimedOnCommand(duration_seconds=300))
    info = dict(rig.registry.snapshot.device_info[("Flower Room", "main", name)])
    info["control_mode"] = "manual"
    decision = rig.processor._build_light_decision(
        location="Flower Room",
        cluster="main",
        device_name=name,
        device_info=info,
        current_time=rig.clock.now,
        is_sun=False,
        failsafe_active=False,
    )
    await rig.processor.device_controller.process_device(
        "Flower Room",
        "main",
        name,
        info,
        {},
        rig.clock.now,
        {"light_intensity": decision.effective_percent / 100.0, "light_decision": decision},
    )
    row = next(row for row in _flower_relays(rig) if row.assignment.device_name == name)
    assert row.observed_state is True and row.command_mode == "timed_on"
    assert rig.dimmer.values[(2, 0)] == 10


@pytest.mark.asyncio
async def test_off_and_auto_release_cancel_the_timed_light_override():
    rig = _rig()
    await rig.commands.initialize_startup()
    await rig.board.sample()
    name = "light_f_1"
    await rig.commands.execute("Flower Room", "main", name, TimedOnCommand(duration_seconds=300))
    await _tick(rig)
    await rig.commands.execute("Flower Room", "main", name, ManualOffCommand())
    await _tick(rig)
    row = next(row for row in _flower_relays(rig) if row.assignment.device_name == name)
    assert row.observed_state is False and row.command_mode == "manual_off"
    assert rig.dimmer.values[(2, 0)] == 0
    await rig.commands.execute("Flower Room", "main", name, AutoCommand())
    await _tick(rig)
    row = next(row for row in _flower_relays(rig) if row.assignment.device_name == name)
    assert row.observed_state is False and row.command_mode == "auto"
    assert row.command_expires_at is None


@pytest.mark.asyncio
async def test_failsafe_still_overrides_a_timed_light_command(monkeypatch):
    monkeypatch.setenv("FAILSAFE_ENFORCEMENT_ENABLED", "true")
    rig = _rig()
    await rig.commands.initialize_startup()
    await rig.board.sample()
    await rig.commands.execute(
        "Flower Room", "main", "light_f_1", TimedOnCommand(duration_seconds=300)
    )
    rig.redis.failsafe = True
    await _tick(rig)
    assert all(row.observed_state is False for row in _flower_relays(rig))
    assert rig.dimmer.values == {(2, 0): 0, (1, 1): 0, (2, 1): 0}
