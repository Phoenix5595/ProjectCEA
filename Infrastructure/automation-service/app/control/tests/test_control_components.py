"""Unit tests for control engine components."""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from shared.logging import get_logger

# Import the components we're testing
from app.control.sensor_data_manager import SensorDataManager
from app.control.setpoint_manager import SetpointManager, RampManager
from app.control.pid_controller_manager import PIDControllerManager
from app.control.device_controller import DeviceController

logger = get_logger(__name__)


class TestSensorDataManager:
    """Test the SensorDataManager class."""

    def test_init(self):
        """Test SensorDataManager initialization."""
        mock_db = MagicMock()
        manager = SensorDataManager(mock_db)
        assert manager.database == mock_db

    @pytest.mark.asyncio
    async def test_get_sensor_values(self):
        """Test getting sensor values for a location/cluster."""
        mock_db = MagicMock()
        mock_db.get_sensor_value = AsyncMock(side_effect=[25.5, 60.0, None])

        manager = SensorDataManager(mock_db)

        sensor_mapping = {
            'TestLocation': {
                'TestCluster': {
                    'temperature': 'temp_sensor_1',
                    'humidity': 'humidity_sensor_1',
                    'co2': 'co2_sensor_1'
                }
            }
        }

        values = await manager.get_sensor_values('TestLocation', 'TestCluster', sensor_mapping)

        assert values['temp_sensor_1'] == 25.5
        assert values['humidity_sensor_1'] == 60.0
        assert values['co2_sensor_1'] is None

        # Verify database calls
        assert mock_db.get_sensor_value.call_count == 3

    def test_get_sensor_for_setpoint_type(self):
        """Test getting sensor name for setpoint type."""
        mock_db = MagicMock()
        manager = SensorDataManager(mock_db)

        sensor_mapping = {
            'TestLocation': {
                'TestCluster': {
                    'temperature': 'temp_sensor_1',
                    'humidity': 'humidity_sensor_1'
                }
            }
        }

        # Test temperature sensor
        sensor = manager.get_sensor_for_setpoint_type(
            sensor_mapping, 'TestLocation', 'TestCluster', 'heating'
        )
        assert sensor == 'temp_sensor_1'

        # Test unknown setpoint type
        sensor = manager.get_sensor_for_setpoint_type(
            sensor_mapping, 'TestLocation', 'TestCluster', 'unknown'
        )
        assert sensor is None


class TestRampManager:
    """Test the RampManager class."""

    def test_init(self):
        """Test RampManager initialization."""
        manager = RampManager()
        assert manager.active_ramps == {}

    def test_start_ramp(self):
        """Test starting a ramp transition."""
        manager = RampManager()
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        manager.start_ramp('heating', 20.0, 25.0, 60.0, current_time)

        assert 'heating' in manager.active_ramps
        ramp = manager.active_ramps['heating']
        assert ramp.start_value == 20.0
        assert ramp.target_value == 25.0
        assert ramp.duration_minutes == 60.0

    def test_get_ramp_value_in_progress(self):
        """Test getting ramp value during transition."""
        manager = RampManager()
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        current_time = start_time + timedelta(minutes=30)  # Halfway through

        manager.start_ramp('heating', 20.0, 25.0, 60.0, start_time)

        value, progress = manager.get_ramp_value('heating', 25.0, current_time)

        # Should be halfway: (20 + 25) / 2 = 22.5
        assert value == 22.5
        assert progress == 0.5

    def test_get_ramp_value_complete(self):
        """Test getting ramp value after completion."""
        manager = RampManager()
        start_time = datetime(2023, 1, 1, 12, 0, 0)
        current_time = start_time + timedelta(minutes=65)  # Past completion

        manager.start_ramp('heating', 20.0, 25.0, 60.0, start_time)

        value, progress = manager.get_ramp_value('heating', 25.0, current_time)

        # Should return target value and None progress (ramp cleaned up)
        assert value == 25.0
        assert progress is None
        assert 'heating' not in manager.active_ramps

    def test_cancel_ramp(self):
        """Test canceling a ramp."""
        manager = RampManager()
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        manager.start_ramp('heating', 20.0, 25.0, 60.0, current_time)
        assert 'heating' in manager.active_ramps

        manager.cancel_ramp('heating')
        assert 'heating' not in manager.active_ramps


class TestSetpointManager:
    """Test SetpointManager class."""

    def test_init(self):
        """Test SetpointManager initialization."""
        mock_db = MagicMock()
        manager = SetpointManager(mock_db)
        assert isinstance(manager.ramp_manager, RampManager)

    def test_extract_nominal_setpoints(self):
        """Test extracting nominal setpoints from data."""
        mock_db = MagicMock()
        manager = SetpointManager(mock_db)

        setpoint_data = {
            'heating_setpoint': 22.0,
            'cooling_setpoint': 28.0,
            'humidity': 65.0,
            'co2': 800.0,
            'vpd': 1.2
        }

        nominal = manager._extract_nominal_setpoints(setpoint_data)

        assert nominal['heating'] == 22.0
        assert nominal['cooling'] == 28.0
        assert nominal['humidity'] == 65.0
        assert nominal['co2'] == 800.0
        assert nominal['vpd'] == 1.2

    @pytest.mark.asyncio
    async def test_compute_effective_setpoints_no_ramp(self):
        """Test computing effective setpoints without ramping."""
        mock_db = MagicMock()
        manager = SetpointManager(mock_db)
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        setpoint_data = {
            'heating_setpoint': 22.0,
            'cooling_setpoint': 28.0,
            'ramp_in_duration': 0  # No ramping
        }

        result = await manager.compute_effective_setpoints(
            'TestLocation', 'TestCluster', current_time, 'DAY',
            setpoint_data, None, None
        )

        assert result['effective_heating_setpoint'] == 22.0
        assert result['effective_cooling_setpoint'] == 28.0
        assert result['nominal_heating_setpoint'] == 22.0
        assert result['nominal_cooling_setpoint'] == 28.0

    @pytest.mark.asyncio
    async def test_compute_effective_setpoints_with_ramp(self):
        """Test computing effective setpoints with ramping."""
        mock_db = MagicMock()
        manager = SetpointManager(mock_db)
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        setpoint_data = {
            'heating_setpoint': 25.0,  # Target
            'ramp_in_duration': 60.0
        }

        sensor_values = {'temp_sensor': 20.0}  # Starting point

        result = await manager.compute_effective_setpoints(
            'TestLocation', 'TestCluster', current_time, 'PRE_DAY',
            setpoint_data, sensor_values, 'DAY'  # Mode change
        )

        # Should start ramping from sensor value (20.0) to target (25.0)
        assert result['effective_heating_setpoint'] == 20.0  # Initial value
        assert result['nominal_heating_setpoint'] == 25.0
        assert result['ramp_progress_heating'] == 0.0  # Just started


class TestPIDControllerManager:
    """Test the PIDControllerManager class."""

    def test_init(self):
        """Test PIDControllerManager initialization."""
        mock_db = MagicMock()
        manager = PIDControllerManager(mock_db)
        assert manager.database == mock_db
        assert manager._pid_controllers == {}

    @pytest.mark.asyncio
    async def test_get_pid_controller_none_for_non_pid_device(self):
        """Test that non-PID devices return None controller."""
        mock_db = MagicMock()
        manager = PIDControllerManager(mock_db)

        controller = await manager.get_pid_controller(
            'location', 'cluster', 'device', 'light'  # Lights don't use PID
        )

        assert controller is None

    @pytest.mark.asyncio
    async def test_get_pid_controller_with_pid_params(self):
        """Test getting PID controller with valid parameters."""
        mock_db = MagicMock()
        mock_db.get_pid_parameters = AsyncMock(return_value={
            'kp': 1.0, 'ki': 0.1, 'kd': 0.05
        })

        manager = PIDControllerManager(mock_db)

        controller = await manager.get_pid_controller(
            'location', 'cluster', 'device', 'heating'
        )

        assert controller is not None
        assert controller.kp == 1.0
        assert controller.ki == 0.1
        assert controller.kd == 0.05

    @pytest.mark.asyncio
    async def test_process_pid_control(self):
        """Test PID control processing."""
        mock_db = MagicMock()
        mock_db.get_pid_parameters = AsyncMock(return_value={
            'kp': 1.0, 'ki': 0.0, 'kd': 0.0
        })

        manager = PIDControllerManager(mock_db)

        device_info = {'device_type': 'heating'}
        sensor_values = {'temp_sensor': 20.0}
        context = {'effective_heating_setpoint': 22.0}
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        # Mock the PID controller methods
        with patch('app.control.pid_controller.PIDController') as mock_pid_class:
            mock_controller = MagicMock()
            mock_controller.calculate.return_value = 0.8
            mock_pid_class.return_value = mock_controller

            output = await manager.process_pid_control(
                'location', 'cluster', 'device', device_info,
                sensor_values, current_time, context
            )

            assert output == 0.8
            mock_controller.calculate.assert_called_once()

    def test_calculate_error_heating(self):
        """Test error calculation for heating."""
        mock_db = MagicMock()
        manager = PIDControllerManager(mock_db)

        error = manager._calculate_error('heating', 22.0, 20.0)
        assert error == 2.0  # setpoint - sensor = 22 - 20

    def test_calculate_error_cooling(self):
        """Test error calculation for cooling."""
        mock_db = MagicMock()
        manager = PIDControllerManager(mock_db)

        error = manager._calculate_error('cooling', 25.0, 28.0)
        assert error == -3.0  # sensor - setpoint = 28 - 25


class TestDeviceController:
    """Test the DeviceController class."""

    def test_init(self):
        """Test DeviceController initialization."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        mock_dfr = MagicMock()

        controller = DeviceController(mock_relay, mock_db, mock_dfr)

        assert controller.relay_manager == mock_relay
        assert controller.database == mock_db
        assert controller.dfr0971_manager == mock_dfr

    def test_determine_control_mode_manual(self):
        """Test determining manual control mode."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        device_info = {'control_mode': 'manual'}
        context = {}

        mode, setpoint = controller._determine_control_mode('device', device_info, context)

        assert mode == 'manual'
        assert setpoint is None

    def test_determine_control_mode_auto(self):
        """Test determining auto control mode."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        device_info = {'device_type': 'heating', 'control_mode': 'auto'}
        context = {'effective_heating_setpoint': 22.0}

        mode, setpoint = controller._determine_control_mode('device', device_info, context)

        assert mode == 'auto'
        assert setpoint == 22.0

    @pytest.mark.asyncio
    async def test_calculate_rule_based_output_heating(self):
        """Test rule-based output calculation for heating."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        device_info = {'device_type': 'heating', 'hysteresis': 1.0}
        sensor_values = {'temp_sensor': 20.0}  # Below setpoint
        setpoint = 22.0

        output = await controller._calculate_rule_based_output(
            'loc', 'clu', 'dev', device_info, sensor_values, setpoint
        )

        assert output == 1.0  # Should turn on heating

    @pytest.mark.asyncio
    async def test_calculate_rule_based_output_heating_within_hysteresis(self):
        """Test rule-based output within hysteresis band."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        device_info = {'device_type': 'heating', 'hysteresis': 1.0}
        sensor_values = {'temp_sensor': 21.5}  # Within hysteresis of 22.0
        setpoint = 22.0

        output = await controller._calculate_rule_based_output(
            'loc', 'clu', 'dev', device_info, sensor_values, setpoint
        )

        assert output is None  # Should maintain current state

    @pytest.mark.asyncio
    async def test_apply_control_output_binary_device(self):
        """Test applying control output to binary device."""
        mock_relay = MagicMock()
        mock_relay.set_channel_state = AsyncMock(return_value=True)
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        device_info = {'device_type': 'heating', 'channel': 5}
        current_time = datetime(2023, 1, 1, 12, 0, 0)

        await controller._apply_control_output(
            'loc', 'clu', 'device', device_info, 1.0, current_time
        )

        mock_relay.set_channel_state.assert_called_once_with(5, 1)

    def test_find_sensor_by_type(self):
        """Test finding sensor by type keywords."""
        mock_relay = MagicMock()
        mock_db = MagicMock()
        controller = DeviceController(mock_relay, mock_db)

        sensor_values = {
            'temperature_sensor': 25.0,
            'temp_backup': 24.5,
            'humidity_sensor': 60.0
        }

        # Find temperature sensor
        temp_sensor = controller._find_sensor_by_type(sensor_values, ['temperature', 'temp'])
        assert temp_sensor == 25.0

        # Find humidity sensor
        humidity_sensor = controller._find_sensor_by_type(sensor_values, ['humidity'])
        assert humidity_sensor == 60.0

        # Find non-existent sensor
        missing_sensor = controller._find_sensor_by_type(sensor_values, ['co2'])
        assert missing_sensor is None


if __name__ == '__main__':
    pytest.main([__file__])