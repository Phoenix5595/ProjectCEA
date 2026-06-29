"""Tests for the unassigned-channel guard.

Two guards are exercised:

1. ``RelayManager.set_channel_state`` — refuses writes to channels not present
   in ``self._channel_map``. Logs WARNING and returns False; hardware is NOT
   touched.

2. ``DeviceController.restore_device_states`` — skips DB rows whose ``channel``
   is not in the current ``relay_manager._channel_map`` (defense-in-depth; the
   method is currently not called at startup).

These tests run with no real hardware, database, or Redis.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure service root + Infrastructure/ are importable. tests/ -> automation-service/ -> Infrastructure/.
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_INFRA_ROOT = _SERVICE_ROOT.parent
if str(_INFRA_ROOT) not in sys.path:
    sys.path.insert(0, str(_INFRA_ROOT))

from app.control.device_controller import DeviceController
from app.control.relay_manager import RelayManager


# ---------------------------------------------------------------------------
# RelayManager.set_channel_state guard
# ---------------------------------------------------------------------------

def _bare_relay_manager(channel_map: dict[int, tuple[str, str, str]]) -> tuple[RelayManager, MagicMock]:
    """Build a RelayManager with a specific _channel_map, bypassing __init__.

    __init__ calls _build_device_maps which needs a real device_config and
    mcp23017 driver. We bypass that for unit tests.

    Returns the manager and the mcp23017 mock so tests can introspect calls.
    """
    rm = RelayManager.__new__(RelayManager)
    mcp = MagicMock()
    mcp.set_channel = MagicMock(return_value=True)
    rm.mcp23017 = mcp
    rm.device_config = {}
    rm.interlock_manager = MagicMock()
    rm._device_map = {v: k for k, v in channel_map.items()}
    rm._channel_map = dict(channel_map)
    rm._device_info = {}
    rm._current_states = {}
    rm._current_modes = {}
    return rm, mcp


class TestSetChannelStateGuard:
    """Channel not in _channel_map is refused; hardware is not touched."""

    def test_unmapped_channel_returns_false(self):
        rm, mcp = _bare_relay_manager({1: ("Flower Room", "main", "exhaust_fan")})
        result = asyncio.run(rm.set_channel_state(11, 1))
        assert result is False
        mcp.set_channel.assert_not_called()

    def test_mapped_channel_returns_true_and_calls_hardware(self):
        rm, mcp = _bare_relay_manager({1: ("Flower Room", "main", "exhaust_fan")})
        result = asyncio.run(rm.set_channel_state(1, 1))
        assert result is True
        mcp.set_channel.assert_called_once_with(1, True)

    def test_unmapped_channel_logs_warning(self, caplog):
        rm, _mcp = _bare_relay_manager({1: ("Flower Room", "main", "exhaust_fan")})
        with caplog.at_level(logging.WARNING, logger="app.control.relay_manager"):
            result = asyncio.run(rm.set_channel_state(11, 1))
        assert result is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "Expected a WARNING log when channel is unmapped"
        # Must mention the channel number for debuggability
        assert any("11" in r.getMessage() for r in warnings), (
            f"WARNING must mention the channel number, got: {[r.getMessage() for r in warnings]}"
        )

    def test_unmapped_channel_does_not_pollute_state(self):
        """Refusing the write must not leave a stale entry in _current_states."""
        rm, _mcp = _bare_relay_manager({1: ("Flower Room", "main", "exhaust_fan")})
        asyncio.run(rm.set_channel_state(11, 1))
        assert 11 not in rm._current_states
        # The legitimate channel is also untouched
        assert ("Flower Room", "main", "exhaust_fan") not in rm._current_states

    def test_mapped_channel_off(self):
        """Happy path for OFF transition on a mapped channel."""
        rm, mcp = _bare_relay_manager({3: ("Flower Room", "main", "light_1")})
        result = asyncio.run(rm.set_channel_state(3, 0))
        assert result is True
        mcp.set_channel.assert_called_once_with(3, False)
        assert rm._current_states[("Flower Room", "main", "light_1")] == 0


# ---------------------------------------------------------------------------
# DeviceController.restore_device_states guard
# ---------------------------------------------------------------------------

def _make_controller_with_db(device_states: dict) -> tuple[DeviceController, MagicMock]:
    relay_manager = MagicMock()
    relay_manager._channel_map = {1: ("Flower Room", "main", "exhaust_fan")}
    relay_manager.set_channel_state = AsyncMock(return_value=True)
    database = MagicMock()
    database.device_repo = MagicMock()
    database.device_repo.get_device_states = AsyncMock(return_value=device_states)
    controller = DeviceController(relay_manager, database, binary_hysteresis=0.1)
    return controller, relay_manager


class TestRestoreDeviceStatesGuard:
    """restore_device_states must skip rows whose channel is unmapped."""

    @pytest.mark.asyncio
    async def test_skips_unmapped_channel(self):
        """A row with channel=11 (no device mapped) must not call set_channel_state."""
        controller, rm = _make_controller_with_db(
            {
                "exhaust_fan": {"channel": 1, "state": 1},
                "ghost_device": {"channel": 11, "state": 1},  # ch 11 not in map
            }
        )
        await controller.restore_device_states("Flower Room", "main")
        # Only the mapped channel was written
        assert rm.set_channel_state.await_count == 1
        rm.set_channel_state.assert_awaited_with(1, 1)
        # Hardware write for channel 11 must not have happened
        for call in rm.set_channel_state.await_args_list:
            args, _kwargs = call
            assert args[0] != 11, f"set_channel_state was called for unmapped channel 11: {args}"

    @pytest.mark.asyncio
    async def test_skips_unmapped_channel_logs_warning(self, caplog):
        """The skip should emit a WARNING identifying the unmapped channel."""
        controller, rm = _make_controller_with_db(
            {"ghost_device": {"channel": 11, "state": 1}}
        )
        with caplog.at_level(logging.WARNING, logger="app.control.device_controller"):
            await controller.restore_device_states("Flower Room", "main")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("11" in r.getMessage() for r in warnings), (
            f"Expected a WARNING referencing channel 11, got: "
            f"{[r.getMessage() for r in warnings]}"
        )
        # No set_channel_state calls at all (the only row is unmapped)
        rm.set_channel_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_mapped_channel_still_restored(self):
        """Mapped rows are restored normally (regression check on the guard)."""
        controller, rm = _make_controller_with_db(
            {"exhaust_fan": {"channel": 1, "state": 0}}
        )
        await controller.restore_device_states("Flower Room", "main")
        rm.set_channel_state.assert_awaited_once_with(1, 0)

    @pytest.mark.asyncio
    async def test_mix_of_mapped_and_unmapped(self):
        """Three mapped + two unmapped → exactly three hardware writes, all to mapped channels."""
        rm = MagicMock()
        rm._channel_map = {
            1: ("Flower Room", "main", "exhaust_fan"),
            3: ("Flower Room", "main", "light_1"),
            5: ("Flower Room", "main", "light_3"),
        }
        rm.set_channel_state = AsyncMock(return_value=True)
        database = MagicMock()
        database.device_repo = MagicMock()
        database.device_repo.get_device_states = AsyncMock(return_value={
            "exhaust_fan": {"channel": 1, "state": 1},
            "light_1": {"channel": 3, "state": 0},
            "light_3": {"channel": 5, "state": 1},
            "ghost_a": {"channel": 11, "state": 1},
            "ghost_b": {"channel": 12, "state": 0},
        })
        controller = DeviceController(rm, database, binary_hysteresis=0.1)
        await controller.restore_device_states("Flower Room", "main")
        called_channels = {call.args[0] for call in rm.set_channel_state.await_args_list}
        assert called_channels == {1, 3, 5}
        assert rm.set_channel_state.await_count == 3

    @pytest.mark.asyncio
    async def test_none_channel_still_skipped_safely(self):
        """A row with channel=None should not crash; it stays skipped (was already guarded)."""
        controller, rm = _make_controller_with_db(
            {"missing_channel": {"channel": None, "state": 1}}
        )
        await controller.restore_device_states("Flower Room", "main")
        rm.set_channel_state.assert_not_called()
