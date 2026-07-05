"""Tests for raw channel override auto-expiry sweep in ControlEngine.

Verifies:
- _expire_raw_channel_overrides checks each channel 0-15 for expired Redis overrides
- Expired overrides turn the channel OFF and delete the Redis key
- Future overrides are left alone
- Missing keys are skipped
- run_control_loop calls _expire_raw_channel_overrides after _expire_manual_overrides
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
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


def _make_control_engine() -> tuple[ControlEngine, MagicMock, MagicMock, MagicMock]:
    """Build a ControlEngine with mocked dependencies."""
    relay_manager = MagicMock()
    relay_manager.get_device_state = MagicMock(return_value=1)
    relay_manager.set_device_state = MagicMock(return_value=(True, None))
    relay_manager.get_channel = MagicMock(return_value=3)
    relay_manager.set_channel_state = AsyncMock(return_value=True)

    redis_client = MagicMock()
    redis_client.get = MagicMock(return_value=None)
    redis_client.delete = MagicMock(return_value=1)

    automation_redis = MagicMock()
    automation_redis.redis_client = redis_client

    database = MagicMock()
    database._automation_redis = automation_redis
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

    return engine, relay_manager, database, redis_client


class TestExpireRawChannelOverrides:
    """_expire_raw_channel_overrides sweeps expired raw channel overrides."""

    @pytest.mark.asyncio
    async def test_expired_override_turns_off_and_deletes_key(self):
        """An expired override turns channel OFF and deletes the Redis key."""
        engine, relay_manager, _database, redis_client = _make_control_engine()
        past = datetime.now(UTC) - timedelta(seconds=10)

        def _get_side_effect(key: str) -> str | None:
            if key == "cea:relay:manual_override:0":
                return json.dumps({"expires_at": past.isoformat(), "state": 1})
            return None

        redis_client.get = MagicMock(side_effect=_get_side_effect)

        await engine._expire_raw_channel_overrides()

        relay_manager.set_channel_state.assert_awaited_once_with(0, 0)
        redis_client.delete.assert_called_once_with("cea:relay:manual_override:0")

    @pytest.mark.asyncio
    async def test_future_override_left_alone(self):
        """A future override is left alone (no set_channel_state call)."""
        engine, relay_manager, _database, redis_client = _make_control_engine()
        future = datetime.now(UTC) + timedelta(seconds=10)
        redis_client.get = MagicMock(
            return_value=json.dumps({"expires_at": future.isoformat(), "state": 1})
        )

        await engine._expire_raw_channel_overrides()

        relay_manager.set_channel_state.assert_not_awaited()
        redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_key_skipped(self):
        """When no override key exists, nothing happens."""
        engine, relay_manager, _database, redis_client = _make_control_engine()
        redis_client.get = MagicMock(return_value=None)

        await engine._expire_raw_channel_overrides()

        relay_manager.set_channel_state.assert_not_awaited()
        redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_channels_only_expired_processed(self):
        """Only expired channels are processed; future/missing are skipped."""
        engine, relay_manager, _database, redis_client = _make_control_engine()
        past = datetime.now(UTC) - timedelta(seconds=10)
        future = datetime.now(UTC) + timedelta(seconds=10)

        def _get_side_effect(key: str) -> str | None:
            if key == "cea:relay:manual_override:3":
                return json.dumps({"expires_at": past.isoformat(), "state": 1})
            if key == "cea:relay:manual_override:5":
                return json.dumps({"expires_at": future.isoformat(), "state": 1})
            return None

        redis_client.get = MagicMock(side_effect=_get_side_effect)

        await engine._expire_raw_channel_overrides()

        # Only channel 3 is expired
        relay_manager.set_channel_state.assert_awaited_once_with(3, 0)
        redis_client.delete.assert_called_once_with("cea:relay:manual_override:3")

    @pytest.mark.asyncio
    async def test_no_redis_client_returns_early(self):
        """When redis_client is None, method returns early without error."""
        engine, relay_manager, database, _redis_client = _make_control_engine()
        database._automation_redis.redis_client = None

        await engine._expire_raw_channel_overrides()

        relay_manager.set_channel_state.assert_not_awaited()


class TestRunControlLoopRawExpiryIntegration:
    """run_control_loop integrates _expire_raw_channel_overrides safely."""

    @pytest.mark.asyncio
    async def test_calls_expire_raw_after_manual_expire(self):
        """run_control_loop calls _expire_raw_channel_overrides after _expire_manual_overrides."""
        engine, relay_manager, database, redis_client = _make_control_engine()
        past = datetime.now(UTC) - timedelta(seconds=10)
        redis_client.get = MagicMock(
            return_value=json.dumps({"expires_at": past.isoformat(), "state": 1})
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

        # Both expiry methods were called
        database.control_action_repo.get_expired_manual_overrides.assert_awaited_once()
        relay_manager.set_channel_state.assert_awaited()
        redis_client.delete.assert_called()
        engine._log_automation_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_in_raw_expire_does_not_kill_loop(self):
        """An exception in _expire_raw_channel_overrides is caught and logged."""
        engine, relay_manager, database, redis_client = _make_control_engine()
        redis_client.get = MagicMock(side_effect=RuntimeError("Redis connection lost"))

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

        # Manual expiry still called
        database.control_action_repo.get_expired_manual_overrides.assert_awaited_once()
        # Loop continues to log automation state
        engine._log_automation_state.assert_awaited_once()
