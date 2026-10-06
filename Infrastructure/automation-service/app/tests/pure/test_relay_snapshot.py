from __future__ import annotations

import json
from typing import Any

import pytest
from relay_snapshot_fakes import (
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

from app.control.relay_manager import RelayCommandReason, RelayManager


class RecordingSink:
    def __init__(self) -> None:
        self.events = []

    def emit_nowait(self, event) -> None:
        self.events.append(event)


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
    control_engine.alarm_manager = None
    control_engine.device_command_service = None
    control_engine._tick_effective_setpoints = {}
    control_engine._photoperiod_phases = {}

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
async def test_hardware_state_endpoint_returns_narrow_board_snapshot_projection() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager
    from app.routes.hardware import relay_state

    board_state = RelayBoardStateManager(SamplingMcp([(False,) * 16]), Redis())
    assert await board_state.sample() is True

    response = await relay_state(board_state)

    assert set(response) == {"channels", "sampled_at", "changed_at", "freshness", "stale_since"}
    assert response["channels"] == [False] * 16
    assert response["freshness"] == "FRESH"


@pytest.mark.asyncio
async def test_relay_command_reason_requires_a_successful_changed_command() -> None:
    sink = RecordingSink()
    mcp: Any = Mcp([True, False, True])
    relay_manager: Any = RelayManager(mcp, Registry(), Interlocks(), event_sink=sink)
    reason = RelayCommandReason(
        "pid.binary_transition",
        "PID heating relay ON: sensor 20°C; target 22°C; output 65% crossed >60% ON threshold.",
    )

    first_success, _ = await relay_manager.set_device_state(
        "Veg Room", "main", "heater", 1, command_reason=reason
    )
    failed, _ = await relay_manager.set_device_state(
        "Veg Room", "main", "heater", 0, command_reason=reason
    )
    unchanged_success, _ = await relay_manager.set_device_state(
        "Veg Room", "main", "heater", 1, command_reason=reason
    )

    assert first_success is True
    assert failed is False
    assert unchanged_success is True
    assert mcp.writes == [(2, True), (2, False), (2, True)]
    assert [event.event_type for event in sink.events] == [
        "relay.commanded",
        "relay.command_failed",
    ]
    assert sink.events[0].reason_code == "pid.binary_transition"
    assert sink.events[0].reason_text == reason.reason_text
    assert sink.events[1].reason_code is None
    assert sink.events[1].reason_text is None


@pytest.mark.asyncio
async def test_pid_reason_is_omitted_for_mode_only_relay_change() -> None:
    sink = RecordingSink()
    mcp: Any = Mcp([True, True])
    relay_manager: Any = RelayManager(mcp, Registry(), Interlocks(), event_sink=sink)
    reason = RelayCommandReason(
        "pid.binary_transition",
        "PID heating relay ON: output 65% crossed >60% ON threshold.",
    )

    first_success, _ = await relay_manager.set_device_state(
        "Veg Room", "main", "heater", 1, mode="auto", command_reason=reason
    )
    mode_changed_success, _ = await relay_manager.set_device_state(
        "Veg Room", "main", "heater", 1, mode="manual", command_reason=reason
    )

    assert first_success is True
    assert mode_changed_success is True
    assert mcp.writes == [(2, True), (2, True)]
    assert [event.event_type for event in sink.events] == [
        "relay.commanded",
        "relay.commanded",
    ]
    assert sink.events[0].reason_code == "pid.binary_transition"
    assert sink.events[1].reason_code is None
    assert sink.events[1].reason_text is None


@pytest.mark.asyncio
async def test_interlock_and_manual_relay_commands_do_not_invent_pid_causes() -> None:
    class BlockingInterlocks:
        def check_interlock(self, *_args, **_kwargs) -> tuple[bool, str]:
            return False, "blocked"

    reason = RelayCommandReason(
        "pid.binary_transition",
        "PID heating relay ON: output 65% crossed >60% ON threshold.",
    )
    blocked_sink = RecordingSink()
    blocked_mcp: Any = Mcp([True])
    blocked_manager: Any = RelayManager(
        blocked_mcp, Registry(), BlockingInterlocks(), event_sink=blocked_sink
    )
    blocked, _ = await blocked_manager.set_device_state(
        "Veg Room", "main", "heater", 1, command_reason=reason
    )

    manual_sink = RecordingSink()
    manual_mcp: Any = Mcp([True])
    manual_manager: Any = RelayManager(
        manual_mcp, Registry(), Interlocks(), event_sink=manual_sink
    )
    manual_success = await manual_manager.set_channel_state(2, 1)

    assert blocked is False
    assert blocked_mcp.writes == []
    assert blocked_sink.events == []
    assert manual_success is True
    assert manual_mcp.writes == [(2, True)]
    assert len(manual_sink.events) == 1
    assert manual_sink.events[0].event_type == "relay.commanded"
    assert manual_sink.events[0].reason_code is None
    assert manual_sink.events[0].reason_text is None


@pytest.mark.asyncio
async def test_unmatched_relay_observation_remains_unattributed() -> None:
    from app.control.relay_board_state_manager import RelayBoardStateManager

    sink = RecordingSink()
    off = (False,) * 16
    on = (True,) + (False,) * 15
    board_state = RelayBoardStateManager(
        SamplingMcp([off, on]), Redis(), event_sink=sink
    )

    assert await board_state.sample() is True
    assert await board_state.sample() is True

    assert len(sink.events) == 1
    assert sink.events[0].event_type == "relay.observed"
    assert sink.events[0].reason_code is None
    assert sink.events[0].reason_text is None
    assert sink.events[0].payload.observed_state is True
