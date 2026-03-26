"""Control loop resilience tests (climate periods + light schedule).

Legacy tests referenced ``scheduler.get_climate_mode`` and Redis ``read_schedule_state`` in the
control loop; those paths were removed. Climate setpoints now come from ``climate_periods``;
photoperiod bounds for ``is_sun`` come from ``get_room_light_schedule`` (no Redis fallback in-loop).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, create_autospec, patch

import pytest

from app.automation.rules_engine import RulesEngine
from app.config import ConfigLoader
from app.control.control_engine import ControlEngine
from app.control.device_processor import DeviceProcessor
from app.control.relay_manager import RelayManager
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient


def _effective_dict() -> dict:
    return {
        "effective_heating_setpoint": 20.0,
        "effective_cooling_setpoint": 25.0,
        "effective_humidity_setpoint": None,
        "effective_co2_setpoint": None,
        "effective_vpd_setpoint": 1.0,
        "nominal_heating_setpoint": 20.0,
        "nominal_cooling_setpoint": 25.0,
        "nominal_humidity_setpoint": None,
        "nominal_co2_setpoint": None,
        "nominal_vpd_setpoint": 1.0,
        "ramp_progress_heating": None,
        "ramp_progress_cooling": None,
        "ramp_progress_humidity": None,
        "ramp_progress_co2": None,
        "ramp_progress_vpd": None,
    }


@pytest.fixture
def mock_dependencies():
    relay_manager = Mock(spec=RelayManager)
    database = Mock(spec=DatabaseManager)
    config = Mock(spec=ConfigLoader)
    scheduler = Mock(spec=Scheduler)
    rules_engine = Mock(spec=RulesEngine)

    database._automation_redis = create_autospec(AutomationRedisClient, instance=True)
    database._automation_redis.redis_enabled = True

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
async def test_light_schedule_db_error_still_runs_period_from_climate_periods(mock_dependencies):
    """DB failure for room light schedule leaves is_sun False; climate period still drives mode."""
    database = mock_dependencies["database"]
    scheduler = mock_dependencies["scheduler"]
    config = mock_dependencies["config"]
    rules_engine = mock_dependencies["rules_engine"]

    database.schedule_repo = Mock()
    database.schedule_repo.get_room_light_schedule = AsyncMock(side_effect=Exception("DB Failure"))
    database.climate_periods_repo = Mock()
    database.climate_periods_repo.get_active_period = AsyncMock(
        return_value={
            "period_name": "DAY",
            "ramp_minutes": 0,
            "heating_setpoint": 20.0,
            "cooling_setpoint": 25.0,
            "vpd_setpoint": 1.0,
            "co2_setpoint": 800,
        }
    )
    database.setpoint_repo = Mock()
    database.setpoint_repo.log_effective_setpoints = AsyncMock()
    database.control_action_repo = Mock()
    database.control_action_repo.log_automation_state = AsyncMock()

    with patch("app.control.control_engine.DeviceProcessor", spec=DeviceProcessor) as MockProcessor:
        mock_processor_instance = MockProcessor.return_value
        mock_processor_instance.process_devices = AsyncMock()

        engine = ControlEngine(
            relay_manager=mock_dependencies["relay_manager"],
            database=database,
            config=config,
            scheduler=scheduler,
            rules_engine=rules_engine,
        )
        engine.device_processor = mock_processor_instance
        engine.setpoint_manager.compute_effective_setpoints = AsyncMock(return_value=_effective_dict())

        await engine.run_control_loop()

        database.schedule_repo.get_room_light_schedule.assert_called_with("Veg Room", "main")
        database.climate_periods_repo.get_active_period.assert_called()
        mock_processor_instance.process_devices.assert_called()
        call_args = mock_processor_instance.process_devices.call_args
        assert call_args[0][6] == "DAY"
        assert call_args[1]["is_sun"] is False


@pytest.mark.asyncio
async def test_no_climate_period_falls_through_to_no_period(mock_dependencies):
    """When no row matches time, current_mode is NO_PERIOD (not legacy NIGHT)."""
    database = mock_dependencies["database"]
    scheduler = mock_dependencies["scheduler"]
    config = mock_dependencies["config"]
    rules_engine = mock_dependencies["rules_engine"]

    database.schedule_repo = Mock()
    database.schedule_repo.get_room_light_schedule = AsyncMock(return_value=None)
    database.climate_periods_repo = Mock()
    database.climate_periods_repo.get_active_period = AsyncMock(return_value=None)
    database.setpoint_repo = Mock()
    database.setpoint_repo.log_effective_setpoints = AsyncMock()
    database.control_action_repo = Mock()
    database.control_action_repo.log_automation_state = AsyncMock()

    with patch("app.control.control_engine.DeviceProcessor", spec=DeviceProcessor) as MockProcessor:
        mock_processor_instance = MockProcessor.return_value
        mock_processor_instance.process_devices = AsyncMock()

        engine = ControlEngine(
            relay_manager=mock_dependencies["relay_manager"],
            database=database,
            config=config,
            scheduler=scheduler,
            rules_engine=rules_engine,
        )
        engine.device_processor = mock_processor_instance

        await engine.run_control_loop()

        mock_processor_instance.process_devices.assert_called()
        call_args = mock_processor_instance.process_devices.call_args
        assert call_args[0][5] is None
        assert call_args[0][6] == "NO_PERIOD"
