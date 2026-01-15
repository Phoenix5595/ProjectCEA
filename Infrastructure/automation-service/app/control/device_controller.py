"""Device Controller - Handles device state control and validation."""
from typing import Dict, Optional, Any
from datetime import datetime
from shared.logging import get_logger, LoggingContext

logger = get_logger(__name__)


class DeviceController:
    """Handles device control operations and state management."""

    def __init__(self, relay_manager, database_manager, dfr0971_manager=None):
        """Initialize device controller.

        Args:
            relay_manager: Relay manager for device control
            database_manager: Database manager for state persistence
            dfr0971_manager: Optional DFR0971 manager for dimmable lights
        """
        self.relay_manager = relay_manager
        self.database = database_manager
        self.dfr0971_manager = dfr0971_manager

    async def process_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime,
        context: Dict[str, Any]
    ) -> None:
        """Process control for a single device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
        """
        with LoggingContext(operation="process_device"):
            device_type = device_info.get('device_type', '')

            # Log when processing dimmable lights
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                logger.info(f"Processing device {device_name} ({location}/{cluster})")

            # Determine control mode and setpoint
            control_mode, setpoint = self._determine_control_mode(device_name, device_info, context)

            if control_mode == 'manual':
                # Skip automated control for manual devices
                logger.debug(f"Skipping {device_name} ({location}/{cluster}) - manual mode")
                return

            # Calculate control output
            control_output = await self._calculate_control_output(
                location, cluster, device_name, device_info, sensor_values,
                control_mode, setpoint, context
            )

            if control_output is not None:
                # Apply the control output
                await self._apply_control_output(
                    location, cluster, device_name, device_info, control_output, current_time
                )

    def _determine_control_mode(self, device_name: str, device_info: Dict[str, Any],
                               context: Dict[str, Any]) -> tuple[str, Optional[float]]:
        """Determine the control mode and setpoint for a device.

        Returns:
            Tuple of (control_mode, setpoint)
        """
        # Check if device is in failsafe mode
        failsafe_active = context.get('failsafe_active', False)
        if failsafe_active:
            return 'failsafe', None

        # Check device-specific control mode
        device_mode = device_info.get('control_mode', 'auto')

        if device_mode == 'manual':
            return 'manual', None

        # Get setpoint based on device type
        device_type = device_info.get('device_type', '')
        setpoint = self._get_setpoint_for_device_type(device_type, context)

        return 'auto', setpoint

    def _get_setpoint_for_device_type(self, device_type: str, context: Dict[str, Any]) -> Optional[float]:
        """Get the appropriate setpoint for a device type."""
        setpoint_mapping = {
            'heating': context.get('effective_heating_setpoint'),
            'cooling': context.get('effective_cooling_setpoint'),
            'humidifier': context.get('effective_humidity_setpoint'),
            'dehumidifier': context.get('effective_humidity_setpoint'),
            'co2': context.get('effective_co2_setpoint'),
            'light': context.get('light_intensity')  # For dimmable lights
        }

        return setpoint_mapping.get(device_type)

    async def _calculate_control_output(
        self, location: str, cluster: str, device_name: str, device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]], control_mode: str, setpoint: Optional[float],
        context: Dict[str, Any]
    ) -> Optional[float]:
        """Calculate the control output for a device.

        Args:
            location, cluster, device_name: Device identifiers
            device_info: Device configuration
            sensor_values: Available sensor values
            control_mode: Control mode (auto, manual, failsafe)
            setpoint: Target setpoint value
            context: Automation context

        Returns:
            Control output value (0.0-1.0) or None
        """
        device_type = device_info.get('device_type', '')

        # Handle different control modes
        if control_mode == 'failsafe':
            return await self._calculate_failsafe_output(device_type)
        elif control_mode == 'auto':
            # For dimmable lights, use the scheduled light intensity directly
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                light_intensity = context.get('light_intensity')
                if light_intensity is not None:
                    return light_intensity
                # If no intensity in context, return None (don't change current state)
                return None
            
            # Use PID control or rule-based control for other devices
            pid_output = context.get('pid_output')
            if pid_output is not None:
                return pid_output

            # Rule-based control for non-PID devices
            return await self._calculate_rule_based_output(
                location, cluster, device_name, device_info, sensor_values, setpoint
            )

        return None

    async def _calculate_failsafe_output(self, device_type: str) -> Optional[float]:
        """Calculate failsafe output for a device type."""
        failsafe_mapping = {
            'heating': 0.0,  # Turn off heating in failsafe
            'cooling': 0.0,  # Turn off cooling in failsafe
            'humidifier': 0.0,  # Turn off humidifier in failsafe
            'dehumidifier': 0.0,  # Turn off dehumidifier in failsafe
            'co2': 0.0,  # Turn off CO2 in failsafe
            'light': 0.0,  # Turn off lights in failsafe
            'fan': 0.0,  # Turn off fans in failsafe
        }

        return failsafe_mapping.get(device_type)

    async def _calculate_rule_based_output(
        self, location: str, cluster: str, device_name: str, device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]], setpoint: Optional[float]
    ) -> Optional[float]:
        """Calculate rule-based control output for non-PID devices."""
        device_type = device_info.get('device_type', '')

        if setpoint is None:
            return None

        # Get current sensor value
        sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
        if sensor_value is None:
            return None

        # Apply hysteresis and calculate output
        hysteresis = device_info.get('hysteresis', 1.0)  # Default 1.0 degree/unit

        if device_type in ['heating']:
            # Heating: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            # Within hysteresis band, maintain current state
            return None

        elif device_type in ['cooling']:
            # Cooling: turn on if above setpoint + hysteresis
            if sensor_value > (setpoint + hysteresis):
                return 1.0
            elif sensor_value < setpoint:
                return 0.0
            return None

        elif device_type in ['humidifier']:
            # Humidifier: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            return None

        elif device_type in ['dehumidifier']:
            # Dehumidifier: turn on if above setpoint + hysteresis
            if sensor_value > (setpoint + hysteresis):
                return 1.0
            elif sensor_value < setpoint:
                return 0.0
            return None

        elif device_type in ['co2']:
            # CO2: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            return None

        return None

    def _get_sensor_value_for_device(self, device_type: str, sensor_values: Dict[str, Optional[float]]) -> Optional[float]:
        """Get the appropriate sensor value for a device type."""
        sensor_mapping = {
            'heating': lambda: self._find_sensor_by_type(sensor_values, ['temperature', 'temp']),
            'cooling': lambda: self._find_sensor_by_type(sensor_values, ['temperature', 'temp']),
            'humidifier': lambda: self._find_sensor_by_type(sensor_values, ['humidity']),
            'dehumidifier': lambda: self._find_sensor_by_type(sensor_values, ['humidity']),
            'co2': lambda: self._find_sensor_by_type(sensor_values, ['co2'])
        }

        getter = sensor_mapping.get(device_type)
        if getter:
            return getter()

        return None

    def _find_sensor_by_type(self, sensor_values: Dict[str, Optional[float]], type_keywords: list) -> Optional[float]:
        """Find a sensor value by type keywords."""
        for sensor_name, value in sensor_values.items():
            if value is not None:
                sensor_name_lower = sensor_name.lower()
                if any(keyword in sensor_name_lower for keyword in type_keywords):
                    return value
        return None

    async def _apply_control_output(
        self, location: str, cluster: str, device_name: str, device_info: Dict[str, Any],
        control_output: float, current_time: datetime
    ) -> None:
        """Apply the calculated control output to the device.

        Args:
            location, cluster, device_name: Device identifiers
            device_info: Device configuration
            control_output: Control output value (0.0-1.0)
            current_time: Current timestamp
        """
        device_type = device_info.get('device_type', '')
        channel = device_info.get('channel')

        if channel is None:
            logger.warning(f"No channel configured for {device_name}")
            return

        try:
            # Handle different device types
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                # Dimmable light control
                await self._control_dimmable_light(location, cluster, device_name, device_info, control_output)
            else:
                # Binary relay control
                await self._control_binary_device(location, cluster, device_name, device_type, channel, control_output)

            # Log the control action
            await self._log_control_action(location, cluster, device_name, device_type, channel, control_output, current_time)

        except Exception as e:
            logger.error(f"Failed to control {device_name} ({location}/{cluster}): {e}")

    async def _control_dimmable_light(
        self, location: str, cluster: str, device_name: str, device_info: Dict[str, Any], intensity: float
    ) -> None:
        """Control a dimmable light with relay synchronization.
        
        The relay provides POWER to the light, the dimmer provides 0-10V SIGNAL.
        - If intensity > 0: Turn relay ON first, then set dimmer
        - If intensity = 0: Set dimmer to 0, then turn relay OFF
        """
        if not self.dfr0971_manager:
            logger.warning(f"No DFR0971 manager available for {device_name}")
            return

        board_id = device_info.get('dimming_board_id')
        dimming_channel = device_info.get('dimming_channel')
        relay_channel = device_info.get('channel')  # Relay channel for power control

        if board_id is None or dimming_channel is None:
            logger.warning(f"Incomplete DFR0971 config for {device_name}: board_id={board_id}, channel={dimming_channel}")
            return

        try:
            # Convert 0.0-1.0 to 0-100% intensity
            intensity_percent = round(intensity * 100)
            
            # CRITICAL: Sync relay state with dimmer
            # Relay ON when intensity > 0, OFF when intensity = 0
            if relay_channel is not None and self.relay_manager:
                relay_state = 1 if intensity > 0 else 0
                
                if intensity > 0:
                    # Turn relay ON first, then set dimmer (power before signal)
                    self.relay_manager.set_device_state(location, cluster, device_name, 1)
                    self.dfr0971_manager.set_intensity(board_id, dimming_channel, intensity_percent)
                else:
                    # Set dimmer to 0 first, then turn relay OFF (signal before power)
                    self.dfr0971_manager.set_intensity(board_id, dimming_channel, 0)
                    self.relay_manager.set_device_state(location, cluster, device_name, 0)
                
                logger.info(f"Relay channel {relay_channel} set to {'ON' if relay_state else 'OFF'} for {device_name}")
            else:
                # No relay configured, just set dimmer
                self.dfr0971_manager.set_intensity(board_id, dimming_channel, intensity_percent)
            
            # Calculate voltage (0-10V range)
            voltage = intensity * 10.0
            
            # Write to Redis so API returns current state
            if self.database and hasattr(self.database, '_automation_redis') and self.database._automation_redis:
                try:
                    self.database._automation_redis.write_light_intensity(
                        location, cluster, device_name,
                        intensity_percent, voltage, board_id, dimming_channel
                    )
                except Exception as redis_err:
                    logger.warning(f"Failed to write light intensity to Redis: {redis_err}")

            logger.info(
                f"Set {device_name} ({location}/{cluster}) to {intensity_percent}% "
                f"(intensity: {intensity})"
            )

        except Exception as e:
            logger.error(f"Failed to set dimmable light {device_name}: {e}")

    async def _control_binary_device(
        self, location: str, cluster: str, device_name: str, device_type: str, channel: int, output: float
    ) -> None:
        """Control a binary (on/off) device."""
        # Convert output to binary state
        state = 1 if output > 0.5 else 0

        # Apply the state
        success = await self.relay_manager.set_channel_state(channel, state)

        if success:
            logger.info(f"{device_name} ({location}/{cluster}) set to {'ON' if state else 'OFF'}")
        else:
            logger.warning(f"Failed to set {device_name} ({location}/{cluster}) state")

    async def _log_control_action(
        self, location: str, cluster: str, device_name: str, device_type: str,
        channel: int, control_output: float, current_time: datetime
    ) -> None:
        """Log a control action to the database."""
        try:
            # Calculate binary state from control output
            new_state = 1 if control_output > 0.5 else 0
            
            await self.database.log_control_action(
                location=location,
                cluster=cluster,
                device_name=device_name,
                channel=channel,
                old_state=None,  # TODO: Track previous state if needed
                new_state=new_state,
                mode="auto",
                reason=f"Automated control: {device_type}"
            )
        except Exception as e:
            logger.warning(f"Failed to log control action for {device_name}: {e}")

    async def restore_device_states(self, location: str, cluster: str) -> None:
        """Restore device states from database after restart."""
        try:
            device_states = await self.database.get_device_states(location, cluster)

            restored_count = 0
            for device_name, state_info in device_states.items():
                try:
                    channel = state_info.get('channel')
                    state = state_info.get('state', 0)

                    if channel is not None:
                        success = await self.relay_manager.set_channel_state(channel, state)
                        if success:
                            restored_count += 1
                            logger.info(f"Restored {device_name} ({location}/{cluster}) to state {state}")
                        else:
                            logger.warning(f"Failed to restore {device_name} ({location}/{cluster})")

                except Exception as e:
                    logger.warning(f"Error restoring state for {device_name}: {e}")

            logger.info(f"Restored {restored_count} device states for {location}/{cluster}")

        except Exception as e:
            logger.error(f"Failed to restore device states for {location}/{cluster}: {e}")

    def get_device_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all controllable devices."""
        # This would aggregate status from relay manager and DFR0971 manager
        status = {}

        if self.relay_manager:
            # Add relay status
            status['relays'] = getattr(self.relay_manager, 'get_status', lambda: {})()

        if self.dfr0971_manager:
            # Add DFR0971 status
            status['dimmable_lights'] = getattr(self.dfr0971_manager, 'get_status', lambda: {})()

        return status