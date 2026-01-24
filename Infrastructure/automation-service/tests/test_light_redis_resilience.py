from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, create_autospec

from app.control.control_engine import ControlEngine
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.control.scheduler import Scheduler
from app.automation.rules_engine import RulesEngine
from app.control.device_processor import DeviceProcessor


@pytest.fixture
def mock_dependencies():
    relay_manager = Mock(spec=RelayManager)
    database = Mock(spec=DatabaseManager)
    config = Mock(spec=ConfigLoader)
    scheduler = Mock(spec=Scheduler)
    rules_engine = Mock(spec=RulesEngine)

    from app.redis import AutomationRedisClient

    database._automation_redis = create_autospec(AutomationRedisClient, instance=True)
    database._automation_redis.redis_enabled = True

    # Mock config.get_devices
    config.get_devices.return_value = {
        "Veg Room": {
            "main": {
                "light_1": {
                    "device_type": "light",
                    "dimming_enabled": True,
                    "dimming_type": "dfr0971",
                    "dimming_board_id": 0,
                    "dimming_channel": 0,
                }
            }
        }
    }
    config.get_sensor_mapping.return_value = {}

    return {
        "relay_manager": relay_manager,
        "database": database,
        "config": config,
        "scheduler": scheduler,
        "rules_engine": rules_engine,
    }


@pytest.mark.asyncio
async def test_redis_fallback_when_db_fails(mock_dependencies):
    """Test that ControlEngine falls back to Redis when Database fails to provide light schedule."""
    # Setup
    database = mock_dependencies["database"]
    scheduler = mock_dependencies["scheduler"]
    config = mock_dependencies["config"]
    rules_engine = mock_dependencies["rules_engine"]

    # Mock DB failure
    database.get_light_schedule = AsyncMock(side_effect=Exception("DB Failure"))
    database.get_climate_schedule = AsyncMock(
        return_value={"pre_day_duration": 0, "pre_night_duration": 0}
    )
    database.get_setpoint = AsyncMock(
        return_value={"heating_setpoint": 20.0, "cooling_setpoint": 25.0}
    )
    database.log_effective_setpoints = AsyncMock()
    database.log_automation_state = AsyncMock()

    # Mock Redis success
    redis_schedule = {"day_start_time": "06:00:00", "day_end_time": "18:00:00"}
    database._automation_redis.read_schedule_state.return_value = redis_schedule

    # Mock Scheduler behavior
    scheduler.get_climate_mode.return_value = ("DAY", None, None)

    # Mock DeviceProcessor to track calls
    with patch("app.control.control_engine.DeviceProcessor", spec=DeviceProcessor) as MockProcessor:
        mock_processor_instance = MockProcessor.return_value
        mock_processor_instance.process_devices = AsyncMock()

        # Initialize engine
        engine = ControlEngine(
            relay_manager=mock_dependencies["relay_manager"],
            database=database,
            config=config,
            scheduler=scheduler,
            rules_engine=rules_engine,
        )
        # Manually set the mocked processor instance (since __init__ creates it)
        engine.device_processor = mock_processor_instance

        # Run one loop
        await engine.run_control_loop()

        # Verification
        database._automation_redis.read_schedule_state.assert_called_with("Veg Room", "main")

        # Verify mode was resolved correctly in Scheduler call
        scheduler.get_climate_mode.assert_called()
        args = scheduler.get_climate_mode.call_args[0]
        assert args[3] == "06:00:00"  # day_start_time from Redis
        assert args[4] == "18:00:00"  # day_end_time from Redis

        # Verify DeviceProcessor got the correct mode
        mock_processor_instance.process_devices.assert_called()
        call_args = mock_processor_instance.process_devices.call_args[0]
        # call_args index 6 is current_mode
        assert call_args[6] == "DAY"

        # Verify setpoint logging occurred
        database.log_effective_setpoints.assert_called()


@pytest.mark.asyncio
async def test_safety_night_when_all_fails(mock_dependencies):
    """Test that ControlEngine falls back to NIGHT mode when both DB and Redis fail."""
    # Setup
    database = mock_dependencies["database"]
    scheduler = mock_dependencies["scheduler"]
    config = mock_dependencies["config"]
    rules_engine = mock_dependencies["rules_engine"]

    # Mock DB failure
    database.get_light_schedule = AsyncMock(side_effect=Exception("DB Failure"))
    database.get_climate_schedule = AsyncMock(
        return_value={"pre_day_duration": 0, "pre_night_duration": 0}
    )
    database.get_setpoint = AsyncMock(
        return_value={"heating_setpoint": 20.0, "cooling_setpoint": 25.0}
    )
    database.log_effective_setpoints = AsyncMock()
    database.log_automation_state = AsyncMock()

    # Mock Redis failure (None returned)
    database._automation_redis.read_schedule_state.return_value = None

    # Mock DeviceProcessor to track calls
    with patch("app.control.control_engine.DeviceProcessor", spec=DeviceProcessor) as MockProcessor:
        mock_processor_instance = MockProcessor.return_value
        mock_processor_instance.process_devices = AsyncMock()

        # Initialize engine
        engine = ControlEngine(
            relay_manager=mock_dependencies["relay_manager"],
            database=database,
            config=config,
            scheduler=scheduler,
            rules_engine=rules_engine,
        )
        # Manually set the mocked processor instance
        engine.device_processor = mock_processor_instance

        # Run one loop
        await engine.run_control_loop()

        # Verification
        database._automation_redis.read_schedule_state.assert_called_with("Veg Room", "main")

        # Verify DeviceProcessor got "NIGHT" mode as fallback
        mock_processor_instance.process_devices.assert_called()
        call_args = mock_processor_instance.process_devices.call_args[0]
        assert call_args[6] == "NIGHT"

        # Verify setpoint logging occurred with NIGHT mode
        database.log_effective_setpoints.assert_called()
        log_args = database.log_effective_setpoints.call_args.kwargs
        assert log_args["mode"] == "NIGHT"
