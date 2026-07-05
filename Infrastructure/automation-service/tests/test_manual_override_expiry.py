"""Tests for manual override auto-expiry sweep in ControlEngine.

Verifies:
- _expire_manual_overrides queries expired overrides and reverts each to auto
- run_control_loop calls _expire_manual_overrides at the top with try/except
- Exceptions in _expire_manual_overrides do not propagate out of run_control_loop
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_INFRA_ROOT = _SERVICE_ROOT.parent
if str(_INFRA_ROOT) not in sys.path:
    sys.path.insert(0, str(_INFRA_ROOT))

from app.control.control_engine import ControlEngine


def _make_control_engine() -> tuple[ControlEngine, MagicMock, MagicMock]:
    """Build a ControlEngine with mocked dependencies."""
    relay_manager = MagicMock()
    relay_manager.get_device_state = MagicMock(return_value=1)
    relay_manager.set_device_state = MagicMock(return_value=(True, None))
    relay_manager.get_channel = MagicMock(return_value=3)

    database = MagicMock()
    database._automation_redis = None
    database.control_action_repo.get_expired_manual_overrides = AsyncMock(return_value=[])
    database.control_action_repo.clear_manual_expiry = AsyncMock(return_value=True)
    database.device_repo.set_device_state = AsyncMock(return_value=True)
    database.control_action_repo.log_control_action = AsyncMock(return_value=True)

    config = MagicMock()
    config.get_control_config.return_value = {}
    config.get_devices.return_value = {}
    config.get_sensor_mapping.return_value = {}

    scheduler = MagicMock()
    rules_engine = MagicMock()

    engine = ControlEngine(
        relay_manager=relay_manager,
        database=database,
        config=config,
        scheduler=scheduler,
        rules_engine=rules_engine,
    )
    # Skip ramp restoration for tests
    engine._ramps_restored = True

    return engine, relay_manager, database


class TestExpireManualOverrides:
    """_expire_manual_overrides sweeps expired manual overrides back to auto."""

    @pytest.mark.asyncio
    async def test_reverts_expired_override_to_auto(self):
        """A single expired override is reverted to auto and cleared."""
        engine, relay_manager, database = _make_control_engine()
        database.control_action_repo.get_expired_manual_overrides = AsyncMock(
            return_value=[
                {
                    "location": "Flower Room",
                    "cluster": "main",
                    "device_name": "Exhaust Fan",
                    "channel": 3,
                }
            ]
        )

        await engine._expire_manual_overrides()

        relay_manager.set_device_state.assert_called_once_with(
            "Flower Room", "main", "Exhaust Fan", 0, "auto"
        )
        database.control_action_repo.clear_manual_expiry.assert_awaited_once_with(
            "Flower Room", "main", "Exhaust Fan"
        )

    @pytest.mark.asyncio
    async def test_reverts_multiple_expired_overrides(self):
        """Multiple expired overrides are each reverted and cleared."""
        engine, relay_manager, database = _make_control_engine()
        database.control_action_repo.get_expired_manual_overrides = AsyncMock(
            return_value=[
                {
                    "location": "Flower Room",
                    "cluster": "main",
                    "device_name": "Exhaust Fan",
                    "channel": 3,
                },
                {
                    "location": "Veg Room",
                    "cluster": "main",
                    "device_name": "Humidifier",
                    "channel": 5,
                },
            ]
        )

        await engine._expire_manual_overrides()

        assert relay_manager.set_device_state.call_count == 2
        assert database.control_action_repo.clear_manual_expiry.await_count == 2

    @pytest.mark.asyncio
    async def test_no_expired_overrides_does_nothing(self):
        """When no overrides are expired, nothing is reverted."""
        engine, relay_manager, database = _make_control_engine()
        database.control_action_repo.get_expired_manual_overrides = AsyncMock(return_value=[])

        await engine._expire_manual_overrides()

        relay_manager.set_device_state.assert_not_called()
        database.control_action_repo.clear_manual_expiry.assert_not_awaited()


class TestRunControlLoopExpiryIntegration:
    """run_control_loop integrates _expire_manual_overrides safely."""

    @pytest.mark.asyncio
    async def test_calls_expire_at_top_of_loop(self):
        """run_control_loop calls _expire_manual_overrides before device processing."""
        engine, relay_manager, database = _make_control_engine()
        database.control_action_repo.get_expired_manual_overrides = AsyncMock(
            return_value=[
                {
                    "location": "Flower Room",
                    "cluster": "main",
                    "device_name": "Exhaust Fan",
                    "channel": 3,
                }
            ]
        )

        engine.sensor_reader.read_sensors = AsyncMock(return_value={})
        engine.climate_resolver.resolve_period = AsyncMock(
            return_value={
                "active_period": None,
                "current_period_name": "NO_PERIOD",
                "setpoint_data": None,
                "time_str": "12:00",
            }
        )
        engine.device_processor.process_devices = AsyncMock()
        engine._log_automation_state = AsyncMock()
        engine._is_moon_authority_room_mode = AsyncMock(return_value=False)

        await engine.run_control_loop()

        database.control_action_repo.get_expired_manual_overrides.assert_awaited_once()
        relay_manager.set_device_state.assert_called_once_with(
            "Flower Room", "main", "Exhaust Fan", 0, "auto"
        )
        engine._log_automation_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_in_expire_does_not_kill_loop(self):
        """An exception in _expire_manual_overrides is caught and logged."""
        engine, relay_manager, database = _make_control_engine()
        database.control_action_repo.get_expired_manual_overrides = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )

        engine.sensor_reader.read_sensors = AsyncMock(return_value={})
        engine.climate_resolver.resolve_period = AsyncMock(
            return_value={
                "active_period": None,
                "current_period_name": "NO_PERIOD",
                "setpoint_data": None,
                "time_str": "12:00",
            }
        )
        engine.device_processor.process_devices = AsyncMock()
        engine._log_automation_state = AsyncMock()
        engine._is_moon_authority_room_mode = AsyncMock(return_value=False)

        await engine.run_control_loop()

        database.control_action_repo.get_expired_manual_overrides.assert_awaited_once()
        engine._log_automation_state.assert_awaited_once()
