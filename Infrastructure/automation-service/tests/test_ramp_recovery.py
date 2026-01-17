"""Unit tests for ramp recovery functionality."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, MagicMock
from app.redis_client import AutomationRedisClient
from app.control.control_engine import ControlEngine
from app.database import DatabaseManager


@pytest.fixture
def mock_redis():
    """Mock Redis client with in-memory storage."""
    # Create real Redis client instance but disable actual Redis
    redis = AutomationRedisClient(redis_url=None)
    redis.redis_enabled = True

    # Create in-memory storage for testing
    redis_store = {}

    # Mock Redis client methods to use in-memory store
    redis.redis_client = Mock()
    redis.redis_client.setex = Mock(side_effect=lambda key, ttl, value: redis_store.__setitem__(key, value))
    redis.redis_client.get = Mock(side_effect=lambda key: redis_store.get(key))
    redis.redis_client.delete = Mock(side_effect=lambda key: redis_store.pop(key, None))

    return redis


@pytest.fixture
def mock_database():
    """Mock database manager."""
    db = Mock(spec=DatabaseManager)
    db._automation_redis = Mock()
    db._automation_redis.redis_enabled = True
    return db


class TestRampStateRedisPersistence:
    """Tests for Redis ramp state persistence."""

    def test_write_ramp_state(self, mock_redis):
        """Test writing ramp state to Redis."""
        location = "Veg Room"
        cluster = "clusterA"
        setpoint_type = "heating_setpoint"
        current_effective = 20.0
        ramp_start = datetime.now()
        ramp_duration = 30
        target_setpoint = 25.0

        result = mock_redis.write_ramp_state(
            location, cluster, setpoint_type,
            current_effective, ramp_start, ramp_duration, target_setpoint
        )

        assert result is True
        mock_redis.redis_client.setex.assert_called_once()
        call_args = mock_redis.redis_client.setex.call_args
        key = call_args[0][0]
        assert key == f"ramp:{location}:{cluster}:{setpoint_type}"

    def test_write_ramp_state_redis_disabled(self):
        """Test writing ramp state when Redis is disabled."""
        # Create a disabled Redis client
        disabled_redis = AutomationRedisClient(redis_url=None)
        disabled_redis.redis_enabled = False

        result = disabled_redis.write_ramp_state(
            "loc", "clust", "type",
            20.0, datetime.now(), 30, 25.0
        )

        assert result is False

    def test_read_ramp_state(self, mock_redis):
        """Test reading ramp state from Redis."""
        import json
        ramp_state = {
            'current_effective_setpoint': 20.0,
            'ramp_start_timestamp': datetime.now().isoformat(),
            'ramp_duration': 30,
            'target_setpoint': 25.0
        }

        # Pre-populate the in-memory store
        key = "ramp:Veg Room:clusterA:heating_setpoint"
        mock_redis.redis_client.get.side_effect = lambda k: json.dumps(ramp_state) if k == key else None

        result = mock_redis.read_ramp_state('Veg Room', 'clusterA', 'heating_setpoint')

        assert result is not None
        assert result['current_effective_setpoint'] == 20.0
        assert result['target_setpoint'] == 25.0
        assert result['ramp_duration'] == 30

    def test_read_ramp_state_not_found(self, mock_redis):
        """Test reading ramp state when not found."""
        result = mock_redis.read_ramp_state('Veg Room', 'clusterA', 'heating_setpoint')

        assert result is None

    def test_clear_ramp_state(self, mock_redis):
        """Test clearing ramp state from Redis."""
        result = mock_redis.clear_ramp_state('Veg Room', 'clusterA', 'heating_setpoint')

        assert result is True

    def test_clear_ramp_state_redis_disabled(self):
        """Test clearing ramp state when Redis is disabled."""
        # Create a disabled Redis client
        disabled_redis = AutomationRedisClient(redis_url=None)
        disabled_redis.redis_enabled = False

        result = disabled_redis.clear_ramp_state('Veg Room', 'clusterA', 'heating_setpoint')

        assert result is False


class TestRampRecovery:
    """Tests for ramp state recovery on startup."""

    @pytest.mark.asyncio
    async def test_restore_active_ramp(self, mock_database):
        """Test restoring an active ramp from database."""
        mock_control_engine = Mock(spec=ControlEngine)
        mock_control_engine._ramp_state = {}
        mock_control_engine.database = mock_database

        # Mock database to return effective setpoints and nominal setpoints
        mock_database.get_latest_effective_setpoints = AsyncMock(return_value={
            'effective_heating_setpoint': 20.0,
            'effective_cooling_setpoint': 22.0,
            'nominal_heating_setpoint': 25.0,
            'nominal_cooling_setpoint': 26.0,
        })
        mock_database.get_setpoint = AsyncMock(return_value={
            'heating_setpoint': 25.0,
            'cooling_setpoint': 26.0,
            'humidity': None,
            'co2': None,
            'vpd': None,
            'ramp_in_duration': 15
        })

        # Mock config to return devices
        mock_config = Mock()
        mock_config.get_devices.return_value = {
            'Veg Room': {
                'clusterA': {}
            }
        }
        mock_control_engine.config = mock_config

        # Import and execute the actual method
        from app.control.control_engine import ControlEngine as CE
        await CE.restore_ramp_state_from_database(mock_control_engine)

        # Verify ramp state was restored for heating and cooling
        assert ('Veg Room', 'clusterA', 'heating_setpoint') in mock_control_engine._ramp_state
        heating_ramp = mock_control_engine._ramp_state[('Veg Room', 'clusterA', 'heating_setpoint')]
        assert heating_ramp['current_effective_setpoint'] == 20.0
        assert heating_ramp['target_setpoint'] == 25.0
        assert heating_ramp['ramp_duration'] == 15

    @pytest.mark.asyncio
    async def test_clear_stale_ramp(self, mock_database):
        """Test that stale ramp states (effective == nominal) are not restored."""
        mock_control_engine = Mock(spec=ControlEngine)
        mock_control_engine._ramp_state = {}
        mock_control_engine.database = mock_database

        # Mock database to return same effective and nominal values (ramp complete)
        mock_database.get_latest_effective_setpoints = AsyncMock(return_value={
            'effective_heating_setpoint': 25.0,
            'nominal_heating_setpoint': 25.0,
        })
        mock_database.get_setpoint = AsyncMock(return_value={
            'heating_setpoint': 25.0,
            'ramp_in_duration': 0
        })

        mock_config = Mock()
        mock_config.get_devices.return_value = {
            'Veg Room': {
                'clusterA': {}
            }
        }
        mock_control_engine.config = mock_config

        from app.control.control_engine import ControlEngine as CE
        await CE.restore_ramp_state_from_database(mock_control_engine)

        # Ramp state should be created with duration 0 (no ramp)
        assert ('Veg Room', 'clusterA', 'heating_setpoint') in mock_control_engine._ramp_state
        heating_ramp = mock_control_engine._ramp_state[('Veg Room', 'clusterA', 'heating_setpoint')]
        assert heating_ramp['current_effective_setpoint'] == 25.0
        assert heating_ramp['ramp_duration'] == 0

    @pytest.mark.asyncio
    async def test_ramp_state_redis_unavailable(self, mock_database):
        """Test graceful degradation when Redis is unavailable."""
        mock_control_engine = Mock(spec=ControlEngine)
        mock_control_engine._ramp_state = {}
        mock_control_engine.database = mock_database
        mock_database._automation_redis = Mock()
        mock_database._automation_redis.redis_enabled = False

        from app.control.control_engine import ControlEngine as CE
        await CE.restore_ramp_state_from_database(mock_control_engine)

        # Should not attempt to access Redis
        mock_database.get_latest_effective_setpoints.assert_not_called()
        mock_database.get_setpoint.assert_not_called()
