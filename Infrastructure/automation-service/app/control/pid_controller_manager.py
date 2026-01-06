"""PID Controller Manager - Handles PID control logic for devices."""
from typing import Dict, Optional, Any, Tuple
from datetime import datetime
from shared.logging import get_logger, LoggingContext

logger = get_logger(__name__)


class PIDControllerManager:
    """Manages PID controllers for device control."""

    def __init__(self, database_manager):
        """Initialize PID controller manager.

        Args:
            database_manager: Database manager for PID parameter storage
        """
        self.database = database_manager
        self._pid_controllers: Dict[Tuple[str, str, str, str], Any] = {}
        self._pid_params_cache: Dict[str, Dict[str, float]] = {}
        self._cache_timestamp: Optional[float] = None
        self._params_cache_ttl = 300.0  # Cache PID params for 5 minutes  # PIDController instances

    async def get_pid_controller(self, location: str, cluster: str, device_name: str,
                                device_type: str) -> Optional[Any]:
        """Get or create a PID controller for a device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_type: Device type

        Returns:
            PIDController instance or None if not applicable
        """
        key = (location, cluster, device_name, device_type)

        if key not in self._pid_controllers:
            # Try to create PID controller
            controller = await self._create_pid_controller(location, cluster, device_name, device_type)
            if controller:
                self._pid_controllers[key] = controller

        return self._pid_controllers.get(key)

    async def _create_pid_controller(self, location: str, cluster: str, device_name: str,
                                   device_type: str) -> Optional[Any]:
        """Create a PID controller for a device if applicable.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_type: Device type

        Returns:
            PIDController instance or None
        """
        # Import here to avoid circular imports
        from app.control.pid_controller import PIDController

        # Check if device type supports PID control
        pid_capable_types = ['heating', 'cooling', 'humidifier', 'dehumidifier', 'co2']

        if device_type not in pid_capable_types:
            return None

        try:
            # Get PID parameters with caching
            pid_params = await self._get_cached_pid_parameters(device_type)
            if not pid_params:
                logger.debug(f"No PID parameters found for device_type {device_type}")
                return None

            kp = pid_params.get('kp', 1.0)
            ki = pid_params.get('ki', 0.0)
            kd = pid_params.get('kd', 0.0)

            # Create PID controller with optimized parameters
            controller = PIDController(kp=kp, ki=ki, kd=kd)
            logger.debug(f"Created PID controller for {device_name} ({device_type}): Kp={kp}, Ki={ki}, Kd={kd}")

            return controller

        except Exception as e:
            logger.error(f"Failed to create PID controller for {device_name} ({device_type}): {e}")
            return None

    async def _get_cached_pid_parameters(self, device_type: str) -> Optional[Dict[str, float]]:
        """Get PID parameters with caching to reduce database calls."""
        import asyncio
        current_time = asyncio.get_event_loop().time()

        # Check cache validity
        if (self._cache_timestamp and
            current_time - self._cache_timestamp < self._params_cache_ttl and
            device_type in self._pid_params_cache):
            return self._pid_params_cache[device_type]

        # Cache miss - fetch from database
        try:
            pid_params = await self.database.get_pid_parameters(device_type)
            if pid_params:
                self._pid_params_cache[device_type] = pid_params
                self._cache_timestamp = current_time
            return pid_params
        except Exception as e:
            logger.warning(f"Failed to fetch PID parameters for {device_type}: {e}")
            return None

    async def process_pid_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime,
        context: Dict[str, Any],
        current_mode: Optional[str] = None
    ) -> Optional[float]:
        """Process PID control for a device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
            current_mode: Current climate mode

        Returns:
            Control output value (0.0-1.0) or None if not applicable
        """
        with LoggingContext(operation="process_pid_control"):
            device_type = device_info.get('device_type', '')

            # Get PID controller
            controller = await self.get_pid_controller(location, cluster, device_name, device_type)
            if not controller:
                return None

            # Get effective setpoint from context
            setpoint = self._get_setpoint_for_device(device_type, context)
            if setpoint is None:
                logger.debug(f"No setpoint available for {device_name} ({device_type})")
                return None

            # Get sensor value
            sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
            if sensor_value is None:
                logger.debug(f"No sensor value available for {device_name} ({device_type})")
                return None

            # Check for mode changes (reset integrator if needed)
            climate_mode_key = (location, cluster)
            previous_mode = context.get('previous_climate_mode', {}).get(climate_mode_key)

            if current_mode != previous_mode and previous_mode is not None:
                controller.reset()
                logger.debug(f"PID integrator reset for {device_name} due to mode change: {previous_mode} -> {current_mode}")

            # Calculate PID output
            try:
                error = self._calculate_error(device_type, setpoint, sensor_value)
                output = controller.calculate(error, current_time)

                # Clamp output to valid range
                output = max(0.0, min(1.0, output))

                logger.debug(
                    f"PID {device_name} ({device_type}): setpoint={setpoint}, "
                    f"sensor={sensor_value}, error={error:.3f}, output={output:.3f}"
                )

                return output

            except Exception as e:
                logger.error(f"PID calculation failed for {device_name} ({device_type}): {e}")
                return None

    def _get_setpoint_for_device(self, device_type: str, context: Dict[str, Any]) -> Optional[float]:
        """Get the appropriate setpoint for a device type."""
        setpoint_mapping = {
            'heating': 'effective_heating_setpoint',
            'cooling': 'effective_cooling_setpoint',
            'humidifier': 'effective_humidity_setpoint',
            'dehumidifier': 'effective_humidity_setpoint',
            'co2': 'effective_co2_setpoint'
        }

        setpoint_key = setpoint_mapping.get(device_type)
        if setpoint_key:
            return context.get(setpoint_key)

        return None

    def _get_sensor_value_for_device(self, device_type: str, sensor_values: Dict[str, Optional[float]]) -> Optional[float]:
        """Get the appropriate sensor value for a device type."""
        sensor_mapping = {
            'heating': lambda: self._find_temperature_sensor(sensor_values),
            'cooling': lambda: self._find_temperature_sensor(sensor_values),
            'humidifier': lambda: self._find_humidity_sensor(sensor_values),
            'dehumidifier': lambda: self._find_humidity_sensor(sensor_values),
            'co2': lambda: self._find_co2_sensor(sensor_values)
        }

        getter = sensor_mapping.get(device_type)
        if getter:
            return getter()

        return None

    def _find_temperature_sensor(self, sensor_values: Dict[str, Optional[float]]) -> Optional[float]:
        """Find temperature sensor value."""
        # Look for sensors with 'temperature' in name or 'temp' in name
        for sensor_name, value in sensor_values.items():
            if value is not None and ('temperature' in sensor_name.lower() or 'temp' in sensor_name.lower()):
                return value
        return None

    def _find_humidity_sensor(self, sensor_values: Dict[str, Optional[float]]) -> Optional[float]:
        """Find humidity sensor value."""
        for sensor_name, value in sensor_values.items():
            if value is not None and 'humidity' in sensor_name.lower():
                return value
        return None

    def _find_co2_sensor(self, sensor_values: Dict[str, Optional[float]]) -> Optional[float]:
        """Find CO2 sensor value."""
        for sensor_name, value in sensor_values.items():
            if value is not None and 'co2' in sensor_name.lower():
                return value
        return None

    def _calculate_error(self, device_type: str, setpoint: float, sensor_value: float) -> float:
        """Calculate control error based on device type."""
        if device_type in ['heating']:
            # For heating, error is setpoint - sensor (positive = too cold)
            return setpoint - sensor_value
        elif device_type in ['cooling']:
            # For cooling, error is sensor - setpoint (positive = too hot)
            return sensor_value - setpoint
        elif device_type in ['humidifier']:
            # For humidifier, error is setpoint - sensor (positive = too dry)
            return setpoint - sensor_value
        elif device_type in ['dehumidifier']:
            # For dehumidifier, error is sensor - setpoint (positive = too humid)
            return sensor_value - setpoint
        elif device_type in ['co2']:
            # For CO2, error is setpoint - sensor (positive = too low)
            return setpoint - sensor_value
        else:
            return 0.0

    async def reload_pid_parameters(self, device_type: str) -> bool:
        """Reload PID parameters for all controllers of a device type.

        Args:
            device_type: Device type to reload

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get updated PID parameters
            pid_params = await self.database.get_pid_parameters(device_type)
            if not pid_params:
                logger.warning(f"No PID parameters found for device_type {device_type}")
                return False

            kp = pid_params.get('kp', 1.0)
            ki = pid_params.get('ki', 0.0)
            kd = pid_params.get('kd', 0.0)

            # Update all controllers of this type
            updated_count = 0
            for key, controller in self._pid_controllers.items():
                if key[3] == device_type:  # device_type is the 4th element in key
                    old_kp, old_ki, old_kd = controller.kp, controller.ki, controller.kd
                    controller.update_parameters(kp, ki, kd)
                    updated_count += 1
                    logger.info(
                        f"PID parameters reloaded for {key[2]} ({device_type}): "
                        f"Kp={old_kp}->{kp}, Ki={old_ki}->{ki}, Kd={old_kd}->{kd}"
                    )

            logger.info(f"PID parameters updated for {updated_count} controllers of type {device_type}")
            return True

        except Exception as e:
            logger.error(f"Failed to reload PID parameters for {device_type}: {e}")
            return False

    def get_pid_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all PID controllers."""
        status = {}
        for key, controller in self._pid_controllers.items():
            location, cluster, device_name, device_type = key
            status[f"{location}/{cluster}/{device_name}"] = {
                'device_type': device_type,
                'kp': controller.kp,
                'ki': controller.ki,
                'kd': controller.kd,
                'integral': getattr(controller, 'integral', 0),
                'previous_error': getattr(controller, 'previous_error', 0)
            }
        return status

    def clear_caches(self) -> None:
        """Clear all caches for fresh data."""
        self._pid_params_cache.clear()
        self._cache_timestamp = None
        logger.debug("PID parameter cache cleared")

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics."""
        return {
            'active_controllers': len(self._pid_controllers),
            'cached_params': len(self._pid_params_cache),
            'cache_age_seconds': (
                asyncio.get_event_loop().time() - self._cache_timestamp
                if self._cache_timestamp else None
            )
        }