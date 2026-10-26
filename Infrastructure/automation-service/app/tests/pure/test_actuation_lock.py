"""The shared actuation lock orders the control tick with every external relay command."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from test_photoperiod_history_logger import _engine_with, _room_snapshot

from app.control.device_command_service import DeviceCommandService, TimedOnCommand
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


class _RecordingRelayManager:
    def __init__(self) -> None:
        self.bind_calls: list[RuntimeDeviceSnapshot] = []

    def bind_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> object:
        self.bind_calls.append(snapshot)
        return object()

    async def retry_unresolved(self) -> None:
        return None

    def release_snapshot(self, token: object) -> None:
        return None


class _Registry:
    def __init__(self) -> None:
        self.snapshot = RuntimeDeviceSnapshot.create(
            version=1,
            hierarchy={
                "Veg Room": {"main": {"heater_veg_1": {"device_id": 1, "channel": 2}}},
            },
            mode_parameters={},
            active_modes={},
            light_intensities={},
            light_programs=[],
        )
        self.actuation_lock = asyncio.Lock()

    def subscribe(self, _consumer: Any) -> None:
        return None


class _Relay:
    def __init__(self) -> None:
        self.device_states: list[tuple[str, str, str, int, str]] = []

    def get_device_state(self, _location: str, _cluster: str, _device_name: str) -> int:
        return 0

    async def set_device_state(
        self, location: str, cluster: str, device_name: str, state: int, mode: str
    ) -> tuple[bool, None]:
        self.device_states.append((location, cluster, device_name, state, mode))
        return True, None

    def record_command_state(self, *_args: object) -> None:
        return None


class _History:
    async def log_control_action(self, *_args: object, **_kwargs: object) -> bool:
        return True


@pytest.mark.asyncio
async def test_control_tick_waits_for_the_actuation_lock_and_binds_the_installed_snapshot() -> None:
    # Given: a real tick bounded by in-memory ports, with the actuation lock held.
    engine: Any = _engine_with(
        None, _room_snapshot({"Flower Room": {"main": {}}, "Veg Room": {"main": {}}})
    )
    relay = _RecordingRelayManager()
    engine.relay_manager = relay

    async def no_op(_snapshot: object) -> None:
        return None

    engine._run_control_loop_with_snapshot = no_op
    registry: Any = engine.runtime_device_registry
    await registry.actuation_lock.acquire()
    try:
        # When: the tick starts while another actuation path (a relay move) owns
        # the lock, and that path installs a new snapshot before releasing it.
        tick = asyncio.create_task(engine.run_control_loop())
        await asyncio.sleep(0)

        # Then: it has not bound the relay snapshot yet.
        assert relay.bind_calls == []
        moved = _room_snapshot({"Flower Room": {"main": {}}})
        registry.snapshot = moved
    finally:
        registry.actuation_lock.release()

    await tick
    # And: the tick acts on the snapshot the move installed, never the one it replaced.
    assert relay.bind_calls == [moved]


@pytest.mark.asyncio
async def test_manual_command_waits_for_the_actuation_lock() -> None:
    # Given: a real command service whose registry owns the actuation lock.
    registry = _Registry()
    relay = _Relay()
    service: Any = DeviceCommandService(registry, relay, _History(), None)
    await registry.actuation_lock.acquire()
    try:
        # When: a timed manual command starts while the lock is held.
        pending = asyncio.create_task(
            service.execute("Veg Room", "main", "heater_veg_1", TimedOnCommand(duration_seconds=60))
        )
        await asyncio.sleep(0)

        # Then: the relay write has not happened yet.
        assert relay.device_states == []
    finally:
        registry.actuation_lock.release()

    result = await pending
    assert result.mode == "timed_on"
    assert relay.device_states == [("Veg Room", "main", "heater_veg_1", 1, "timed_on")]
