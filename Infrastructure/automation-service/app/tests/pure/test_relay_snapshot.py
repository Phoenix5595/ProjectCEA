from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from relay_snapshot_fakes import (
    ApiDatabase,
    ApiRedis,
    BoardState,
    ControlActions,
    Interlocks,
    Mcp,
    Redis,
    Registry,
    SamplingMcp,
    SnapshotBinding,
    TickBoardState,
)

from app.control.relay_manager import RelayManager


@pytest.mark.asyncio
async def test_relay_manager_characterization_keeps_prior_state_when_mcp_write_fails() -> None:
    mcp: Any = Mcp([True, False])
    registry: Any = Registry()
    interlocks: Any = Interlocks()
    relay_manager: Any = RelayManager(mcp, registry, interlocks)

    success, _reason = await relay_manager.set_device_state("Veg Room", "main", "heater", 1)
    failed, _reason = await relay_manager.set_device_state("Veg Room", "main", "heater", 0)

    assert success is True
    assert failed is False
    assert relay_manager.get_device_state("Veg Room", "main", "heater") == 1


@pytest.mark.asyncio
async def test_board_snapshot_is_null_until_first_successful_sample() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager

    manager = RelayBoardStateManager(SamplingMcp([None]), Redis())

    sampled = await manager.sample()

    assert sampled is False
    assert manager.get_snapshot().channels is None
    assert manager.get_snapshot().sampled_at is None


@pytest.mark.asyncio
async def test_board_snapshot_persists_only_initial_and_changed_samples() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager

    off = (False,) * 16
    changed = (True,) + (False,) * 15
    redis = Redis()
    manager = RelayBoardStateManager(SamplingMcp([off, off, changed, None]), redis)

    assert await manager.sample() is True
    first = manager.get_snapshot()
    assert await manager.sample() is True
    unchanged = manager.get_snapshot()
    assert await manager.sample() is True
    changed_snapshot = manager.get_snapshot()
    assert await manager.sample() is False

    assert len(redis.set_calls) == 2
    assert first.changed_at[0] is not None
    assert unchanged.changed_at == first.changed_at
    assert changed_snapshot.changed_at[0] != first.changed_at[0]
    assert manager.get_snapshot() == changed_snapshot


@pytest.mark.asyncio
async def test_relay_manager_samples_after_success_and_records_failed_write() -> None:
    board_state = BoardState()
    control_actions = ControlActions()
    mcp: Any = Mcp([True, False])
    registry: Any = Registry()
    interlocks: Any = Interlocks()
    board_state_any: Any = board_state
    control_actions_any: Any = control_actions
    relay_manager: Any = RelayManager(
        mcp,
        registry,
        interlocks,
        relay_board_state_manager=board_state_any,
        control_action_repository=control_actions_any,
    )

    success, _reason = await relay_manager.set_device_state("Veg Room", "main", "heater", 1)
    failed, _reason = await relay_manager.set_device_state("Veg Room", "main", "heater", 0)

    assert success is True
    assert failed is False
    assert board_state.write_samples == 1
    assert relay_manager.get_device_state("Veg Room", "main", "heater") == 1
    assert control_actions.failures == [("Veg Room", "main", "heater", 2, 1, "auto", 0)]


@pytest.mark.asyncio
async def test_startup_restore_preserves_unchanged_channel_transition_times() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager
    from app.redis.schema import RELAY_BOARD_SNAPSHOT

    restored_at = "2026-07-29T10:00:00Z"
    redis = Redis()
    redis.values[RELAY_BOARD_SNAPSHOT] = json.dumps(
        {
            "channels": [False] * 16,
            "sampled_at": restored_at,
            "changed_at": [restored_at] * 16,
        }
    )
    manager = RelayBoardStateManager(SamplingMcp([(False,) * 16]), redis)

    assert await manager.on_startup_restore() is True

    snapshot = manager.get_snapshot()
    assert len(redis.set_calls) == 1
    assert snapshot.sampled_at is not None
    assert snapshot.changed_at[0] is not None
    assert snapshot.changed_at[0].isoformat().replace("+00:00", "Z") == restored_at


def test_mcp_board_sampling_reads_gpio_a_and_b_once_under_one_lock() -> None:
    source = (Path(__file__).parents[2] / "hardware" / "mcp23017.py").read_text()
    sample_all_channels = source.split("    def sample_all_channels", maxsplit=1)[1].split(
        "    def get_all_channels", maxsplit=1
    )[0]

    assert "with self._i2c_lock:" in sample_all_channels
    assert sample_all_channels.count("self.bus.read_byte_data") == 2
    assert "MCP23017_GPIOA" in sample_all_channels
    assert "MCP23017_GPIOB" in sample_all_channels


@pytest.mark.asyncio
async def test_control_tick_samples_in_finally_for_a_noop_tick() -> None:
    from app.control.control_engine import ControlEngine

    relay_manager = SnapshotBinding()
    scheduler = SnapshotBinding()
    board_state = TickBoardState()
    control_engine: Any = object.__new__(ControlEngine)
    control_engine.runtime_device_registry = SnapshotBinding()
    control_engine.relay_manager = relay_manager
    control_engine.scheduler = scheduler
    control_engine.relay_board_state_manager = board_state

    async def no_op(_snapshot: object) -> None:
        return None

    control_engine._run_control_loop_with_snapshot = no_op

    await control_engine.run_control_loop()

    assert board_state.samples == 1
    assert len(relay_manager.released) == 1
    assert len(scheduler.released) == 1


@pytest.mark.asyncio
async def test_raw_relay_write_samples_the_board_after_success() -> None:
    board_state = BoardState()
    mcp: Any = Mcp([True])
    registry: Any = Registry()
    interlocks: Any = Interlocks()
    board_state_any: Any = board_state
    relay_manager: Any = RelayManager(
        mcp, registry, interlocks, relay_board_state_manager=board_state_any
    )

    assert await relay_manager.set_channel_state(2, 1) is True

    assert board_state.write_samples == 1
    assert relay_manager.get_device_state("Veg Room", "main", "heater") == 1


@pytest.mark.asyncio
async def test_hardware_state_endpoint_returns_board_snapshot_and_separate_metadata() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager
    from app.routes.hardware import relay_state

    board_state = RelayBoardStateManager(SamplingMcp([(False,) * 16]), Redis())
    assert await board_state.sample() is True

    automation_redis: Any = ApiRedis()
    database: Any = ApiDatabase()
    response = await relay_state(board_state, automation_redis, database)

    assert set(response) == {"channels", "sampled_at", "changed_at", "control_metadata"}
    assert response["channels"] == [False] * 16
    assert set(response["control_metadata"]) == {"modes", "override_expires_at"}
