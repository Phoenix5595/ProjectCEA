from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _Registry:
    def __init__(self) -> None:
        self.snapshot = RuntimeDeviceSnapshot.create(
            version=1,
            hierarchy={"Veg Room": {"main": {"heater_veg_1": {"device_id": 1, "channel": 2}}}},
            mode_parameters={},
            active_modes={},
            light_intensities={},
            light_programs=[],
        )

    def subscribe(self, _callback: Any) -> None:
        return None


class _Mcp:
    def __init__(self, samples: list[tuple[bool, ...] | None]) -> None:
        self._samples = deque(samples)
        self.writes: list[tuple[int, bool]] = []

    def set_channel(self, channel: int, state: bool) -> bool:
        self.writes.append((channel, state))
        return True

    def sample_all_channels(self) -> tuple[bool, ...] | None:
        return self._samples.popleft()


class _Interlocks:
    def check_interlock(self, *_args: Any, **_kwargs: Any) -> tuple[bool, None]:
        return True, None


class _Alarms:
    def __init__(self) -> None:
        self.raised: list[tuple[str, str, str, str]] = []
        self.cleared: list[tuple[str, str, str]] = []

    def raise_alarm(
        self, location: str, cluster: str, alarm_name: str, severity: str, _message: str
    ) -> bool:
        self.raised.append((location, cluster, alarm_name, severity))
        return True

    def clear_alarm(self, location: str, cluster: str, alarm_name: str) -> bool:
        self.cleared.append((location, cluster, alarm_name))
        return True


class _ControlActions:
    def __init__(self) -> None:
        self.recoveries: list[tuple[str, str, str, int]] = []

    async def record_relay_recovery(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        _state: int,
        _mode: str,
    ) -> bool:
        self.recoveries.append((location, cluster, device_name, channel))
        return True


def _manager(
    samples: list[tuple[bool, ...] | None],
) -> tuple[RelayManager, RelayBoardStateManager, _Clock, _Mcp, _Alarms, _ControlActions]:
    clock = _Clock()
    mcp = _Mcp(samples)
    board = RelayBoardStateManager(mcp, now=clock)
    alarms = _Alarms()
    actions = _ControlActions()
    mcp_port: Any = mcp
    registry_port: Any = _Registry()
    interlocks_port: Any = _Interlocks()
    actions_port: Any = actions
    manager = RelayManager(
        mcp_port,
        registry_port,
        interlocks_port,
        relay_board_state_manager=board,
        control_action_repository=actions_port,
        now=clock,
    )
    return manager, board, clock, mcp, alarms, actions


@pytest.mark.asyncio
async def test_mismatch_enters_syncing_and_retries_once_per_tick() -> None:
    # Given: a successful write whose MCP observations remain OFF.
    off = (False,) * 16
    manager, board, _clock, mcp, _alarms, _actions = _manager([off, off, off])
    assert await board.sample() is True

    # When: the assigned heater is commanded ON, then one tick reconciliation runs.
    assert await manager.set_device_state("Veg Room", "main", "heater_veg_1", 1) == (True, None)
    state = manager.get_channel_control_state(2)
    assert state is not None
    assert state.syncing is True
    await manager.retry_unresolved()

    # Then: the disagreement is visible and exactly one retry is issued for that tick.
    assert mcp.writes == [(2, True), (2, True)]
    state = manager.get_channel_control_state(2)
    assert state is not None
    assert state.syncing is True


@pytest.mark.asyncio
async def test_fresh_agreement_clears_syncing_alarm_and_logs_one_recovery() -> None:
    # Given: a channel that first observes a desired/observed disagreement.
    off = (False,) * 16
    on = (False, False, True) + (False,) * 13
    manager, board, _clock, _mcp, alarms, actions = _manager([off, off, on])
    assert await board.sample() is True
    assert await manager.set_device_state("Veg Room", "main", "heater_veg_1", 1) == (True, None)

    # When: a later fresh MCP sample agrees with the desired state.
    assert await board.sample() is True
    await manager.evaluate_observation(alarms)

    # Then: syncing and its alarm clear and the recovery is audited once.
    state = manager.get_channel_control_state(2)
    assert state is not None
    assert state.syncing is False
    assert alarms.cleared == [("Veg Room", "main", "relay_mismatch_channel_2")]
    assert actions.recoveries == [("Veg Room", "main", "heater_veg_1", 2)]


@pytest.mark.asyncio
async def test_assigned_mismatch_raises_room_critical_after_five_seconds() -> None:
    # Given: an assigned ON command that MCP continues to observe as OFF.
    off = (False,) * 16
    manager, board, clock, _mcp, alarms, _actions = _manager([off, off])
    assert await board.sample() is True
    assert await manager.set_device_state("Veg Room", "main", "heater_veg_1", 1) == (True, None)
    clock.advance(5)

    # When: the observation is evaluated at the mismatch threshold.
    await manager.evaluate_observation(alarms)

    # Then: the device owner's room receives the critical mismatch alarm.
    assert alarms.raised == [("Veg Room", "main", "relay_mismatch_channel_2", "critical")]


@pytest.mark.asyncio
async def test_unassigned_mismatch_raises_system_hardware_warning_after_five_seconds() -> None:
    # Given: an unassigned raw relay whose observed state disagrees with its desired state.
    off = (False,) * 16
    manager, board, clock, _mcp, alarms, _actions = _manager([off, off])
    assert await board.sample() is True
    assert await manager.set_channel_state(5, 1) is True
    clock.advance(5)

    # When: the observation is evaluated at the mismatch threshold.
    await manager.evaluate_observation(alarms)

    # Then: the shared hardware namespace receives a warning rather than a room failsafe.
    assert alarms.raised == [
        ("System", "hardware", "unassigned_relay_mismatch_channel_5", "warning")
    ]


@pytest.mark.asyncio
async def test_failed_sample_is_stale_preserves_last_good_and_allows_only_off() -> None:
    # Given: one fresh all-OFF sample followed by an unreadable MCP board.
    off = (False,) * 16
    manager, board, _clock, _mcp, _alarms, _actions = _manager([off, None, None])
    assert await board.sample() is True
    good_snapshot = board.get_snapshot()

    # When: sampling fails and a new command is attempted.
    assert await board.sample() is False
    on_result = await manager.set_device_state("Veg Room", "main", "heater_veg_1", 1)
    off_result = await manager.set_device_state("Veg Room", "main", "heater_veg_1", 0)

    # Then: last-good observation remains, freshness is STALE, and only OFF is accepted.
    assert board.get_snapshot() == good_snapshot
    assert board.get_freshness().status == "STALE"
    assert on_result == (False, "Relay observation is stale; only OFF commands are allowed")
    assert off_result == (True, None)


@pytest.mark.asyncio
async def test_stale_escalates_from_room_warning_to_critical_after_thirty_seconds() -> None:
    # Given: a room with a previously valid board sample that becomes stale.
    off = (False,) * 16
    manager, board, clock, _mcp, alarms, _actions = _manager([off, None])
    assert await board.sample() is True
    assert await board.sample() is False
    clock.advance(5)

    # When: staleness crosses warning then critical thresholds.
    await manager.evaluate_observation(alarms)
    clock.advance(25)
    await manager.evaluate_observation(alarms)

    # Then: the owning room escalates without replacing the last-good observation.
    assert alarms.raised == [
        ("Veg Room", "main", "relay_board_stale", "warning"),
        ("Veg Room", "main", "relay_board_stale", "critical"),
    ]
