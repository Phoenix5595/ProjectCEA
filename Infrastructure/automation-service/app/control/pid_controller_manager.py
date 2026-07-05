"""PID Controller Manager - Handles PID control logic for devices."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.state import PIDParams, StateManager, get_state_manager
from shared.infra_logging import LoggingContext, get_logger

logger = get_logger(__name__)


class PIDControllerManager:
    """Manages PID controllers for device control."""

    def __init__(self, database_manager):
        """Initialize PID controller manager.

        Args:
            database_manager: Database manager for PID parameter storage
        """
        self.database = database_manager
        self._pid_controllers: dict[tuple[str, str, str, str], Any] = {}
        self._autotuners: dict[str, Any] = {}  # Auto-tuner instances per device type
        # StateManager for fast in-memory PID param access (<1ms reads)
        self._state: StateManager = get_state_manager()

    async def get_pid_controller(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> Any | None:
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
            controller = await self._create_pid_controller(
                location, cluster, device_name, device_type
            )
            if controller:
                self._pid_controllers[key] = controller

        return self._pid_controllers.get(key)

    # Control mode integration
    async def get_control_mode_info(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any]:
        """Get control mode info for a device type with caching."""
        try:
            mode_info = await self.database.pid_repo.get_pid_control_mode(
                location, cluster, device_type
            )
            if mode_info:
                return mode_info
        except Exception as e:
            logger.warning(f"Failed to get control mode for {device_type}: {e}")
        return {"control_mode": "pid", "hysteresis_high": 1.0, "hysteresis_low": 0.5}

    async def get_or_create_autotuner(self, device_type: str) -> Any:
        """Get or create an auto-tuner instance for a device type."""
        from app.control.pid_autotuner import RelayAutoTuner

        if device_type not in self._autotuners:
            self._autotuners[device_type] = RelayAutoTuner(
                relay_amplitude=50.0, hysteresis=0.5, min_cycles=3, max_cycles=10
            )
        return self._autotuners[device_type]

    def _on_off_control(
        self, error: float, hysteresis_high: float, hysteresis_low: float, device_type: str
    ) -> float:
        """Simple ON/OFF control with hysteresis.

        Args:
            error: Control error (setpoint - sensor for heating-like, sensor - setpoint for cooling-like)
            hysteresis_high: Upper threshold to turn ON
            hysteresis_low: Lower threshold to turn OFF
            device_type: Device type for determining direction

        Returns:
            Control output (0.0 or 1.0)
        """
        state_key = f"on_off_state_{device_type}"
        current_state = getattr(self, state_key, False)

        if error > hysteresis_high:
            new_state = True
        elif error < -hysteresis_low:
            new_state = False
        else:
            new_state = current_state

        setattr(self, state_key, new_state)
        return 1.0 if new_state else 0.0

    async def _process_autotune_control(
        self, device_type: str, setpoint: float, sensor_value: float, current_time: datetime
    ) -> float | None:
        """Process auto-tuning control for a device.

        Returns:
            Control output (0.0-1.0) or None if auto-tuning not applicable
        """

        autotuner = await self.get_or_create_autotuner(device_type)

        # Start autotuner if not active
        if not autotuner.is_active:
            autotuner.start(setpoint)
            logger.info(f"Started auto-tuning for {device_type}")

        # Update autotuner and get output
        output, tuning_result = autotuner.update(sensor_value, current_time)

        # Convert relay output (0-100) to normalized (0-1)
        normalized_output = output / 100.0

        # Update autotune state in database
        n_cycles = min(len(autotuner._peaks), len(autotuner._troughs))
        await self.database.pid_repo.update_autotune_state(
            "Flower Room",
            "main",
            device_type,
            is_active=autotuner.is_active,
            cycles_completed=n_cycles,
            status="running" if autotuner.is_active else "calculating",
        )

        # If tuning completed, apply results
        if tuning_result:
            logger.info(
                f"Auto-tuning complete for {device_type}: "
                f"Kp={tuning_result.kp:.3f}, Ki={tuning_result.ki:.4f}, Kd={tuning_result.kd:.3f}"
            )

            # Build change reason
            reason = (
                f"Auto-tune completed after {n_cycles} cycles. "
                f"Ku={tuning_result.ultimate_gain:.2f}, Tu={tuning_result.ultimate_period:.1f}s. "
                f"Method: {tuning_result.tuning_method}"
            )

            # Save new PID parameters with reason
            await self.database.pid_repo.set_pid_parameters_with_reason(
                "Flower Room",
                "main",
                device_type,
                tuning_result.kp,
                tuning_result.ki,
                tuning_result.kd,
                change_reason=reason,
                source="auto_pid",
            )

            # Update autotune state with results
            await self.database.pid_repo.update_autotune_state(
                "Flower Room",
                "main",
                device_type,
                is_active=False,
                current_ku=tuning_result.ultimate_gain,
                current_tu=tuning_result.ultimate_period,
                suggested_kp=tuning_result.kp,
                suggested_ki=tuning_result.ki,
                suggested_kd=tuning_result.kd,
                last_change_reason=reason,
                status="complete",
            )

            # Clear cached PID params from StateManager to force reload
            await self._state.delete(f"pid:parameters:{device_type}")

            # Restart autotuner for continuous tuning
            autotuner.start(setpoint)

        return normalized_output

    async def _create_pid_controller(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> Any | None:
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
        pid_capable_types = ["heating", "cooling", "co2"]

        if device_type not in pid_capable_types:
            return None

        try:
            # Get PID parameters with caching
            pid_params = await self._get_cached_pid_parameters(location, cluster, device_type)
            if not pid_params:
                logger.debug(f"No PID parameters found for device_type {device_type}")
                return None

            kp = pid_params.get("kp", 1.0)
            ki = pid_params.get("ki", 0.0)
            kd = pid_params.get("kd", 0.0)

            # Create PID controller with optimized parameters
            controller = PIDController(kp=kp, ki=ki, kd=kd)
            logger.debug(
                f"Created PID controller for {device_name} ({device_type}): Kp={kp}, Ki={ki}, Kd={kd}"
            )

            return controller

        except Exception as e:
            logger.error(f"Failed to create PID controller for {device_name} ({device_type}): {e}")
            return None

    async def _get_cached_pid_parameters(
        self, location: str, cluster: str, device_type: str
    ) -> PIDParams | None:
        """Get PID parameters from StateManager cache with database fallback.

        Uses StateManager for fast in-memory access (<1ms reads).
        Falls back to database if not in cache.
        """
        # Try StateManager cache first (fast path)
        pid_params = await self._state.get_pid_params(location, cluster, device_type)
        if pid_params:
            logger.debug(
                f"StateManager cache hit for PID params: {location}/{cluster}/{device_type}"
            )
            return pid_params

        # Cache miss - fetch from database
        try:
            pid_params = await self.database.pid_repo.get_pid_parameters(
                location, cluster, device_type
            )
            if pid_params:
                # Populate StateManager cache for future reads
                await self._state.set_pid_params(
                    location,
                    cluster,
                    device_type,
                    pid_params.get("kp", 1.0),
                    pid_params.get("ki", 0.0),
                    pid_params.get("kd", 0.0),
                    binary_hysteresis=pid_params.get("binary_hysteresis"),
                    source="database",
                )
                logger.debug(
                    f"Populated StateManager cache for PID params: {location}/{cluster}/{device_type}"
                )
            return pid_params
        except Exception as e:
            logger.warning(
                f"Failed to fetch PID parameters for {location}/{cluster}/{device_type}: {e}"
            )
            return None

    async def get_binary_hysteresis(self, location: str, cluster: str, device_type: str) -> float:
        """Get binary hysteresis for a location/cluster/device_type with caching.

        Tries StateManager cache first, then database, falling back to
        the global default (0.1) on any error.
        """
        cache_key = f"pid:parameters:{location}:{cluster}:{device_type}"
        try:
            # 1) Try StateManager cache first
            cached = await self._state.get_pid_params(location, cluster, device_type)
            if cached and "binary_hysteresis" in cached:
                return float(cached["binary_hysteresis"])
        except Exception as e:
            logger.debug(f"PID binary_hysteresis cache lookup failed: {e}")

        # 2) Fetch from database
        try:
            result = await self.database.pid_repo.get_binary_hysteresis(
                location, cluster, device_type
            )
            hysteresis = result.get("binary_hysteresis", 0.1)
            # Populate cache for future reads
            try:
                await self._state.set_pid_params(
                    location,
                    cluster,
                    device_type,
                    result.get("kp", 1.0),
                    result.get("ki", 0.0),
                    result.get("kd", 0.0),
                    binary_hysteresis=hysteresis,
                    source="database",
                )
            except Exception as e:
                logger.debug(f"PID binary_hysteresis cache populate failed: {e}")
            return float(hysteresis)
        except Exception as e:
            logger.warning(f"Failed to fetch binary hysteresis for {device_type}: {e}")

        # 3) Global default fallback
        return 0.1

    async def process_pid_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        current_time: datetime,
        context: dict[str, Any],
        current_mode: str | None = None,
    ) -> float | None:
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
            device_type = device_info.get("device_type", "")

            # Get control mode for this device type
            mode_info = await self.get_control_mode_info(location, cluster, device_type)
            control_mode = mode_info.get("control_mode", "pid")

            # Route based on control mode
            if control_mode == "on_off":
                # ON/OFF control with hysteresis
                setpoint = self._get_setpoint_for_device(device_type, context)
                sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
                if setpoint is None or sensor_value is None:
                    return None
                error = self._calculate_error(device_type, setpoint, sensor_value)
                output = self._on_off_control(
                    error,
                    mode_info.get("hysteresis_high", 1.0),
                    mode_info.get("hysteresis_low", 0.5),
                    device_type,
                )
                logger.debug(f"ON/OFF {device_name}: error={error:.2f}, output={output}")
                return output

            elif control_mode == "auto_pid":
                # Auto-tuning control
                setpoint = self._get_setpoint_for_device(device_type, context)
                sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
                if setpoint is None or sensor_value is None:
                    return None
                return await self._process_autotune_control(
                    device_type, setpoint, sensor_value, current_time
                )

            # Standard PID control (control_mode == 'pid')
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
            previous_mode = context.get("previous_climate_mode", {}).get(climate_mode_key)

            # Reset on mode change OR on first tick after restart if mode is known
            # (previous_mode is None implies first tick after service startup)
            should_reset = (current_mode != previous_mode) if previous_mode is not None else True

            if should_reset:
                controller.reset()
                logger.info(
                    f"PID integrator reset for {device_name} (mode: {previous_mode} -> {current_mode})"
                )

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

    def _get_setpoint_for_device(self, device_type: str, context: dict[str, Any]) -> float | None:
        """Get the appropriate setpoint for a device type."""
        setpoint_mapping = {
            "heating": "effective_heating_setpoint",
            "cooling": "effective_cooling_setpoint",
            "humidifier": "effective_humidity_setpoint",
            "dehumidifier": "effective_humidity_setpoint",
            "co2": "effective_co2_setpoint",
        }

        setpoint_key = setpoint_mapping.get(device_type)
        if setpoint_key:
            return context.get(setpoint_key)

        return None

    def _get_sensor_value_for_device(
        self, device_type: str, sensor_values: Mapping[str, float | None]
    ) -> float | None:
        """Get the appropriate sensor value for a device type."""
        sensor_mapping = {
            "heating": lambda: self._find_temperature_sensor(sensor_values),
            "cooling": lambda: self._find_temperature_sensor(sensor_values),
            "humidifier": lambda: self._find_humidity_sensor(sensor_values),
            "dehumidifier": lambda: self._find_humidity_sensor(sensor_values),
            "co2": lambda: self._find_co2_sensor(sensor_values),
        }

        getter = sensor_mapping.get(device_type)
        if getter:
            return getter()

        return None

    def _find_temperature_sensor(self, sensor_values: Mapping[str, float | None]) -> float | None:
        """Find temperature sensor value."""
        # Look for sensors with 'temperature' in name or 'temp' in name
        for sensor_name, value in sensor_values.items():
            if value is not None and (
                "temperature" in sensor_name.lower() or "temp" in sensor_name.lower()
            ):
                return value
        return None

    def _find_humidity_sensor(self, sensor_values: Mapping[str, float | None]) -> float | None:
        """Find humidity sensor value."""
        for sensor_name, value in sensor_values.items():
            if value is not None and "humidity" in sensor_name.lower():
                return value
        return None

    def _find_co2_sensor(self, sensor_values: Mapping[str, float | None]) -> float | None:
        """Find CO2 sensor value."""
        for sensor_name, value in sensor_values.items():
            if value is not None and "co2" in sensor_name.lower():
                return value
        return None

    def _calculate_error(self, device_type: str, setpoint: float, sensor_value: float) -> float:
        """Calculate control error based on device type."""
        if device_type in ["heating"]:
            # For heating, error is setpoint - sensor (positive = too cold)
            return setpoint - sensor_value
        elif device_type in ["cooling"]:
            # For cooling, error is sensor - setpoint (positive = too hot)
            return sensor_value - setpoint
        elif device_type in ["humidifier"]:
            # For humidifier, error is setpoint - sensor (positive = too dry)
            return setpoint - sensor_value
        elif device_type in ["dehumidifier"]:
            # For dehumidifier, error is sensor - setpoint (positive = too humid)
            return sensor_value - setpoint
        elif device_type in ["co2"]:
            # For CO2, error is setpoint - sensor (positive = too low)
            return setpoint - sensor_value
        else:
            return 0.0

    async def reload_pid_parameters(self, location: str, cluster: str, device_type: str) -> bool:
        """Reload PID parameters for all controllers of a device type.

        Args:
            location: Location name
            cluster: Cluster name
            device_type: Device type to reload

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get updated PID parameters
            pid_params = await self.database.pid_repo.get_pid_parameters(
                location, cluster, device_type
            )
            if not pid_params:
                logger.warning(f"No PID parameters found for device_type {device_type}")
                return False

            kp = pid_params.get("kp", 1.0)
            ki = pid_params.get("ki", 0.0)
            kd = pid_params.get("kd", 0.0)

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

            logger.info(
                f"PID parameters updated for {updated_count} controllers of type {device_type}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to reload PID parameters for {device_type}: {e}")
            return False

    def get_pid_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all PID controllers."""
        status = {}
        for key, controller in self._pid_controllers.items():
            location, cluster, device_name, device_type = key
            duty = controller.get_duty_cycle() if hasattr(controller, "get_duty_cycle") else 0.0
            status[f"{location}/{cluster}/{device_name}"] = {
                "device_type": device_type,
                "kp": controller.kp,
                "ki": controller.ki,
                "kd": controller.kd,
                "integral": getattr(controller, "integral", 0),
                "previous_error": getattr(controller, "previous_error", 0),
                "load_percent": round(float(duty), 1),
            }
        return status

    async def clear_caches(self) -> None:
        """Clear all caches for fresh data."""
        # Clear StateManager cache (async)
        await self._state.clear()
        logger.debug("PID parameter cache cleared via StateManager")

    async def get_performance_stats(self) -> dict[str, Any]:
        """Get performance statistics."""
        state_stats = await self._state.get_stats()
        return {
            "active_controllers": len(self._pid_controllers),
            "state_manager_stats": state_stats,
        }
