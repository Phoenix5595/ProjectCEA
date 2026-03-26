"""Device Controller - Handles device state control and validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.infra_logging import LoggingContext, get_logger

if TYPE_CHECKING:
    from ..database import DatabaseManager
    from ..hardware.dfr0971 import DFR0971Manager
    from .hardware_batch import HardwareBatchExecutor
    from .relay_manager import RelayManager

logger = get_logger(__name__)


class DeviceController:
    """Handles device control operations and state management."""

    def __init__(
        self,
        relay_manager: RelayManager,
        database_manager: DatabaseManager,
        dfr0971_manager: DFR0971Manager | None = None,
    ) -> None:
        """Initialize device controller.

        Args:
            relay_manager: Relay manager for device control
            database_manager: Database manager for state persistence
            dfr0971_manager: Optional DFR0971 manager for dimmable lights
        """
        self.relay_manager: RelayManager = relay_manager
        self.database: DatabaseManager = database_manager
        self.dfr0971_manager: DFR0971Manager | None = dfr0971_manager

    async def process_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        current_time: datetime,
        context: dict[str, Any],
        batch_executor: HardwareBatchExecutor | None = None,
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
            batch_executor: Optional batch executor for parallel I2C operations
        """
        with LoggingContext(operation="process_device"):
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                logger.debug(f"Processing device {device_name} ({location}/{cluster})")

            # Determine control mode and setpoint
            control_mode, setpoint = self._determine_control_mode(device_name, device_info, context)

            if control_mode == "manual":
                # Skip automated control for manual devices
                logger.info(f"Skipping {device_name} ({location}/{cluster}) - manual mode")
                return

            # Calculate control output
            control_output = await self._calculate_control_output(
                location,
                cluster,
                device_name,
                device_info,
                sensor_values,
                control_mode,
                setpoint,
                context,
            )

            if control_output is not None:
                # Apply the control output
                await self._apply_control_output(
                    location,
                    cluster,
                    device_name,
                    device_info,
                    control_output,
                    current_time,
                    context,
                    batch_executor,
                )

    def _determine_control_mode(
        self, device_name: str, device_info: dict[str, Any], context: dict[str, Any]
    ) -> tuple[str, float | None]:
        """Determine the control mode and setpoint for a device.

        Returns:
            Tuple of (control_mode, setpoint)
        """
        # Check if device is in failsafe mode
        failsafe_active = context.get("failsafe_active", False)
        if failsafe_active:
            return "failsafe", None

        # Check device-specific control mode
        device_mode = device_info.get("control_mode", "auto")

        if device_mode == "manual":
            return "manual", None

        # Get setpoint based on device type
        device_type = device_info.get("device_type", "")
        setpoint = self._get_setpoint_for_device_type(device_type, context)

        return "auto", setpoint

    def _get_setpoint_for_device_type(
        self, device_type: str, context: dict[str, Any]
    ) -> float | None:
        """Get the appropriate setpoint for a device type.

        VPD is king: humidifier and dehumidifier use effective_vpd_setpoint only.
        """
        setpoint_mapping = {
            "heating": context.get("effective_heating_setpoint"),
            "cooling": context.get("effective_cooling_setpoint"),
            "humidifier": context.get("effective_vpd_setpoint"),
            "dehumidifier": context.get("effective_vpd_setpoint"),
            "co2": context.get("effective_co2_setpoint"),
            "light": context.get("light_intensity"),  # For dimmable lights
        }

        return setpoint_mapping.get(device_type)

    async def _calculate_control_output(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        control_mode: str,
        setpoint: float | None,
        context: dict[str, Any],
    ) -> float | None:
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
        device_type = device_info.get("device_type", "")

        # Handle different control modes
        if control_mode == "failsafe":
            return await self._calculate_failsafe_output(device_type)
        elif control_mode == "auto":
            # Dimmable lights: use sun/moon intensity from context (0 = moon off, >0 = sun)
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                light_intensity = context.get("light_intensity")
                if light_intensity is not None:
                    return light_intensity
                return None

            # Use PID control or rule-based control for other devices
            pid_output = context.get("pid_output")
            if pid_output is not None:
                return pid_output

            # VPD-only control for humidifier and dehumidifier (no humidity setpoint/sensor)
            if device_type in ["humidifier", "dehumidifier"]:
                return self._calculate_vpd_based_output(device_type, context)

            # Rule-based control for non-PID devices (heating, cooling, co2)
            return await self._calculate_rule_based_output(
                location, cluster, device_name, device_info, sensor_values, setpoint
            )

        return None

    def _calculate_vpd_based_output(
        self, device_type: str, context: dict[str, Any]
    ) -> float | None:
        """Calculate control output for humidifier/dehumidifier from VPD only.

        VPD is king: uses effective_vpd_setpoint and current_vpd only.
        No humidity (RH) setpoint or sensor in the decision.

        Uses VPD cascade output if available for intelligent actuator selection.
        Cascade priority: passive ventilation -> dehumidification -> thermal manipulation
        """
        # Check if VPD cascade output is available (from device_processor)
        cascade_output = context.get("vpd_cascade_output")
        if cascade_output is not None:
            # Use cascade output for intelligent actuator selection
            primary_command = cascade_output.primary_command
            actuator_type = primary_command.actuator.value

            # Map device_type to actuator type from cascade
            if device_type == "humidifier":
                # Humidifier should respond when cascade selects HUMIDIFIER
                if actuator_type == "humidifier":
                    return primary_command.output_pct / 100.0  # Convert % to 0-1
                return 0.0  # Off when not selected

            if device_type == "dehumidifier":
                # Dehumidifier should respond when cascade selects DEHUMIDIFIER
                # Note: cascade may also select EXHAUST_FAN (passive ventilation)
                # which takes priority over dehumidifier
                if actuator_type == "dehumidifier":
                    return primary_command.output_pct / 100.0  # Convert % to 0-1
                return 0.0  # Off when cascade selects exhaust_fan or other

            if device_type == "exhaust":
                # Exhaust fan should respond when cascade selects EXHAUST_FAN
                if actuator_type == "exhaust_fan":
                    return primary_command.output_pct / 100.0  # Convert % to 0-1
                return 0.0  # Off when not selected

            return None

        # Fallback: legacy VPD-based control (no cascade available)
        vpd_setpoint = context.get("effective_vpd_setpoint")
        current_vpd = context.get("current_vpd")
        if vpd_setpoint is None or current_vpd is None:
            return None

        # Deadband (kPa) to prevent chatter; same order as control_engine _process_vpd_control
        vpd_deadband = 0.1

        if device_type == "humidifier":
            # VPD too high (dry) -> need moisture -> on
            if current_vpd > vpd_setpoint + vpd_deadband:
                return 1.0
            if current_vpd < vpd_setpoint - vpd_deadband:
                return 0.0
            return None  # In band: maintain current state

        if device_type == "dehumidifier":
            # VPD too low (humid) -> need drying -> on
            if current_vpd < vpd_setpoint - vpd_deadband:
                return 1.0
            if current_vpd > vpd_setpoint + vpd_deadband:
                return 0.0
            return None  # In band: maintain current state

        return None

    async def _calculate_failsafe_output(self, device_type: str) -> float | None:
        """Calculate failsafe output for a device type."""
        failsafe_mapping = {
            "heating": 0.0,  # Turn off heating in failsafe
            "cooling": 0.0,  # Turn off cooling in failsafe
            "humidifier": 0.0,  # Turn off humidifier in failsafe
            "dehumidifier": 0.0,  # Turn off dehumidifier in failsafe
            "co2": 0.0,  # Turn off CO2 in failsafe
            "light": 0.0,  # Turn off lights in failsafe
            "fan": 0.0,  # Turn off fans in failsafe
        }

        return failsafe_mapping.get(device_type)

    async def _calculate_rule_based_output(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        setpoint: float | None,
    ) -> float | None:
        """Calculate rule-based control output for non-PID devices."""
        device_type = device_info.get("device_type", "")

        if setpoint is None:
            return None

        # Get current sensor value
        sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
        if sensor_value is None:
            return None

        # Apply hysteresis and calculate output
        hysteresis = device_info.get("hysteresis", 1.0)  # Default 1.0 degree/unit

        if device_type in ["heating"]:
            # Heating: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            # Within hysteresis band, maintain current state
            return None

        elif device_type in ["cooling"]:
            # Cooling: turn on if above setpoint + hysteresis
            if sensor_value > (setpoint + hysteresis):
                return 1.0
            elif sensor_value < setpoint:
                return 0.0
            return None

        elif device_type in ["humidifier"]:
            # Humidifier: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            return None

        elif device_type in ["dehumidifier"]:
            # Dehumidifier: turn on if above setpoint + hysteresis
            if sensor_value > (setpoint + hysteresis):
                return 1.0
            elif sensor_value < setpoint:
                return 0.0
            return None

        elif device_type in ["co2"]:
            # CO2: turn on if below setpoint - hysteresis
            if sensor_value < (setpoint - hysteresis):
                return 1.0
            elif sensor_value > setpoint:
                return 0.0
            return None

        return None

    def _get_sensor_value_for_device(
        self, device_type: str, sensor_values: Mapping[str, float | None]
    ) -> float | None:
        """Get the appropriate sensor value for a device type."""
        sensor_mapping = {
            "heating": lambda: self._find_sensor_by_type(sensor_values, ["temperature", "temp"]),
            "cooling": lambda: self._find_sensor_by_type(sensor_values, ["temperature", "temp"]),
            "humidifier": lambda: self._find_sensor_by_type(sensor_values, ["humidity"]),
            "dehumidifier": lambda: self._find_sensor_by_type(sensor_values, ["humidity"]),
            "co2": lambda: self._find_sensor_by_type(sensor_values, ["co2"]),
        }

        getter = sensor_mapping.get(device_type)
        if getter:
            return getter()

        return None

    def _find_sensor_by_type(
        self, sensor_values: Mapping[str, float | None], type_keywords: list[str]
    ) -> float | None:
        """Find a sensor value by type keywords."""
        for sensor_name, value in sensor_values.items():
            if value is not None:
                sensor_name_lower = sensor_name.lower()
                if any(keyword in sensor_name_lower for keyword in type_keywords):
                    return value
        return None

    async def _apply_control_output(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        control_output: float,
        current_time: datetime,
        context: dict[str, Any] | None = None,
        batch_executor: HardwareBatchExecutor | None = None,
    ) -> None:
        """Apply the calculated control output to the device.

        Args:
            location, cluster, device_name: Device identifiers
            device_info: Device configuration
            control_output: Control output value (0.0-1.0)
            current_time: Current timestamp
            context: Automation context (for log reason and load_percent)
        """
        device_type = device_info.get("device_type", "")
        channel = device_info.get("channel")

        if channel is None:
            logger.warning(f"No channel configured for {device_name}")
            return

        # Capture old state before applying (for control history log)
        old_state: int | None = None
        if self.relay_manager:
            old_state = self.relay_manager.get_device_state(location, cluster, device_name)
            if old_state is None:
                old_state = 0

        try:
            # Handle different device types
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                # Dimmable light control
                await self._control_dimmable_light(
                    location, cluster, device_name, device_info, control_output, batch_executor
                )
            else:
                # Binary relay control
                await self._control_binary_device(
                    location,
                    cluster,
                    device_name,
                    device_type,
                    channel,
                    control_output,
                    batch_executor,
                )

            # Log the control action
            await self._log_control_action(
                location,
                cluster,
                device_name,
                device_type,
                channel,
                control_output,
                current_time,
                old_state=old_state,
                context=context or {},
            )

        except Exception as e:
            logger.error(f"Failed to control {device_name} ({location}/{cluster}): {e}")

    async def _control_dimmable_light(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        intensity: float,
        batch_executor: HardwareBatchExecutor | None = None,
    ) -> None:
        """Control a dimmable light with relay synchronization.

        The relay provides POWER to the light, the dimmer provides 0-10V SIGNAL.
        - If intensity > 0: Turn relay ON first, then set dimmer
        - If intensity = 0: Set dimmer to 0, then turn relay OFF
        """
        if not self.dfr0971_manager:
            logger.warning(f"No DFR0971 manager available for {device_name}")
            return

        board_id = device_info.get("dimming_board_id")
        dimming_channel = device_info.get("dimming_channel")
        relay_channel = device_info.get("channel")  # Relay channel for power control

        if board_id is None or dimming_channel is None:
            logger.warning(
                f"Incomplete DFR0971 config for {device_name}: board_id={board_id}, channel={dimming_channel}"
            )
            return

        # If batch_executor provided, queue operations for parallel execution
        if batch_executor is not None:
            intensity_percent = round(intensity * 100)
            if intensity > 0:
                batch_executor.queue_light_on(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    intensity=intensity_percent,
                    relay_manager=self.relay_manager,
                    dfr0971_manager=self.dfr0971_manager,
                    board_id=board_id,
                    dimming_channel=dimming_channel,
                    relay_channel=relay_channel,
                )
            else:
                batch_executor.queue_light_off(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    relay_manager=self.relay_manager,
                    dfr0971_manager=self.dfr0971_manager,
                    board_id=board_id,
                    dimming_channel=dimming_channel,
                    relay_channel=relay_channel,
                )
            return

        try:
            # Convert 0.0-1.0 to 0-100% intensity
            intensity_percent = round(intensity * 100)
            relay_ok = True
            dimmer_ok = False

            # CRITICAL: Sync relay state with dimmer
            # Relay ON when intensity > 0, OFF when intensity = 0
            if relay_channel is not None and self.relay_manager:
                if intensity > 0:
                    # Turn relay ON first, then set dimmer (power before signal)
                    relay_ok, relay_reason = self.relay_manager.set_device_state(
                        location, cluster, device_name, 1
                    )
                    if not relay_ok:
                        logger.warning(
                            f"Relay ON failed for {device_name} ({location}/{cluster}): {relay_reason}"
                        )
                    dimmer_ok = self.dfr0971_manager.set_intensity(
                        board_id, dimming_channel, intensity_percent
                    )
                    if not dimmer_ok:
                        logger.warning(
                            f"Dimmer set failed for {device_name} ({location}/{cluster}) "
                            f"board={board_id} ch={dimming_channel}"
                        )
                else:
                    # Set dimmer to 0 first, then turn relay OFF (signal before power)
                    dimmer_ok = self.dfr0971_manager.set_intensity(board_id, dimming_channel, 0)
                    if not dimmer_ok:
                        logger.warning(
                            f"Dimmer set to 0 failed for {device_name} ({location}/{cluster})"
                        )
                    relay_ok, relay_reason = self.relay_manager.set_device_state(
                        location, cluster, device_name, 0
                    )
                    if not relay_ok:
                        logger.warning(
                            f"Relay OFF failed for {device_name} ({location}/{cluster}): {relay_reason}"
                        )
                logger.debug(
                    f"Relay channel {relay_channel} set to {'ON' if intensity > 0 else 'OFF'} for {device_name}"
                )
            else:
                # No relay configured, just set dimmer
                dimmer_ok = self.dfr0971_manager.set_intensity(
                    board_id, dimming_channel, intensity_percent
                )
                if not dimmer_ok:
                    logger.warning(
                        f"Dimmer set failed for {device_name} ({location}/{cluster}) "
                        f"board={board_id} ch={dimming_channel}"
                    )

            # Write to Redis only what hardware actually reflects so Grafana matches reality
            hw_ok = relay_ok and dimmer_ok
            redis_percent = intensity_percent if hw_ok else 0
            redis_voltage = (redis_percent / 100.0) * 10.0

            if (
                self.database
                and hasattr(self.database, "_automation_redis")
                and self.database._automation_redis
            ):
                try:
                    self.database._automation_redis.write_light_intensity(
                        location,
                        cluster,
                        device_name,
                        redis_percent,
                        redis_voltage,
                        board_id,
                        dimming_channel,
                    )
                except Exception as redis_err:
                    logger.warning(f"Failed to write light intensity to Redis: {redis_err}")

            if hw_ok:
                logger.debug(
                    f"Set {device_name} ({location}/{cluster}) to {intensity_percent}% "
                    f"(intensity: {intensity})"
                )
            else:
                logger.warning(
                    f"Hardware control failed for {device_name} ({location}/{cluster}); "
                    f"Redis written as 0% so Grafana shows actual state"
                )

        except Exception as e:
            logger.error(f"Failed to set dimmable light {device_name}: {e}")
            # On exception, write 0 to Redis so UI reflects unknown/off state
            if (
                self.database
                and hasattr(self.database, "_automation_redis")
                and self.database._automation_redis
            ):
                try:
                    self.database._automation_redis.write_light_intensity(
                        location, cluster, device_name, 0, 0.0, board_id, dimming_channel
                    )
                except Exception as redis_err:
                    logger.warning(f"Failed to write 0 to Redis after error: {redis_err}")

    async def _control_binary_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_type: str,
        channel: int,
        output: float,
        batch_executor: HardwareBatchExecutor | None = None,
    ) -> None:
        """Control a binary (on/off) device."""
        # Convert output to binary state
        state = 1 if output > 0.5 else 0

        # If batch_executor provided, queue operation for parallel execution
        if batch_executor is not None and self.relay_manager is not None:
            batch_executor.queue_binary_device(
                location=location,
                cluster=cluster,
                device_name=device_name,
                state=state,
                relay_manager=self.relay_manager,
            )
            return

        # Apply the state directly
        success = await self.relay_manager.set_channel_state(channel, state)

        if success:
            logger.info(f"{device_name} ({location}/{cluster}) set to {'ON' if state else 'OFF'}")
        else:
            logger.warning(f"Failed to set {device_name} ({location}/{cluster}) state")

    def _reason_for_device_type(self, device_type: str, new_state: int) -> str:
        """Human-readable reason for dashboard log."""
        if new_state == 1:
            reasons = {
                "heating": "Heating threshold hit",
                "cooling": "Cooling threshold hit",
                "co2": "CO2 threshold hit",
                "humidifier": "Humidifying",
                "dehumidifier": "Dehumidifying",
            }
            return reasons.get(device_type, f"Automated control: {device_type}")
        reasons_off = {
            "heating": "Heating threshold hit",
            "cooling": "Cooling threshold hit",
            "co2": "CO2 threshold hit",
            "humidifier": "Humidifying",
            "dehumidifier": "Dehumidifying",
        }
        return reasons_off.get(device_type, f"Automated control: {device_type}")

    async def _log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_type: str,
        channel: int,
        control_output: float,
        current_time: datetime,
        old_state: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log a control action to the database."""
        try:
            new_state = 1 if control_output > 0.5 else 0
            reason = self._reason_for_device_type(device_type, new_state)
            load_percent = None
            if context:
                pid_out = context.get("pid_output")
                if pid_out is not None:
                    load_percent = float(pid_out) * 100.0 if 0 <= pid_out <= 1 else float(pid_out)
                    load_percent = max(0.0, min(100.0, load_percent))

            await self.database.control_action_repo.log_control_action(
                location=location,
                cluster=cluster,
                device_name=device_name,
                channel=channel,
                old_state=old_state,
                new_state=new_state,
                mode="auto",
                reason=reason,
                load_percent=load_percent,
            )
        except Exception as e:
            logger.warning(f"Failed to log control action for {device_name}: {e}")

    async def restore_device_states(self, location: str, cluster: str) -> None:
        """Restore device states from database after restart."""
        try:
            device_states = await self.database.device_repo.get_device_states(location, cluster)

            restored_count = 0
            for device_name, state_info in device_states.items():
                try:
                    channel = state_info.get("channel")
                    state = state_info.get("state", 0)

                    if channel is not None:
                        success = await self.relay_manager.set_channel_state(channel, state)
                        if success:
                            restored_count += 1
                            logger.info(
                                f"Restored {device_name} ({location}/{cluster}) to state {state}"
                            )
                        else:
                            logger.warning(
                                f"Failed to restore {device_name} ({location}/{cluster})"
                            )

                except Exception as e:
                    logger.warning(f"Error restoring state for {device_name}: {e}")

            logger.info(f"Restored {restored_count} device states for {location}/{cluster}")

        except Exception as e:
            logger.error(f"Failed to restore device states for {location}/{cluster}: {e}")

    def get_device_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all controllable devices."""
        # This would aggregate status from relay manager and DFR0971 manager
        status = {}

        if self.relay_manager:
            # Add relay status
            status["relays"] = getattr(self.relay_manager, "get_status", lambda: {})()

        if self.dfr0971_manager:
            # Add DFR0971 status
            status["dimmable_lights"] = getattr(self.dfr0971_manager, "get_status", lambda: {})()

        return status
