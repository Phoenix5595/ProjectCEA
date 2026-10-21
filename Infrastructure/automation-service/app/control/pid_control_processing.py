"""PID control and autotuning processing methods."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.control.decision_event_policy import DecisionObservation
from app.redis.schema import pid_key
from shared.infra_logging import LoggingContext, get_logger

logger = get_logger("app.control.pid_controller_manager")


class PIDControlProcessingMixin:
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
            await self._state.delete(pid_key(device_type))

            # Restart autotuner for continuous tuning
            autotuner.start(setpoint)

        return normalized_output

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
            context.pop("pid_decision_observation", None)
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
                    self._event_policy.observe(
                        DecisionObservation(
                            location,
                            cluster,
                            device_name,
                            current_time,
                            "on_off",
                            sensor_value,
                            setpoint,
                            None,
                            None,
                            control_mode,
                            "control.input_missing",
                            "PID control input is unavailable",
                            device_type,
                            self.ramping_now(device_type, context),
                        )
                    )
                    return None
                error = self._calculate_error(device_type, setpoint, sensor_value)
                output = self._on_off_control(
                    error,
                    mode_info.get("hysteresis_high", 1.0),
                    mode_info.get("hysteresis_low", 0.5),
                    device_type,
                )
                logger.debug(f"ON/OFF {device_name}: error={error:.2f}, output={output}")
                observation = DecisionObservation(
                    location,
                    cluster,
                    device_name,
                    current_time,
                    "on_off",
                    sensor_value,
                    setpoint,
                    error,
                    output * 100.0,
                    control_mode,
                    "control.on_off_decision",
                    "ON/OFF hysteresis calculated control output",
                    device_type,
                    self.ramping_now(device_type, context),
                )
                context["pid_decision_observation"] = observation
                self._event_policy.observe(observation)
                return output

            elif control_mode == "auto_pid":
                # Auto-tuning control
                setpoint = self._get_setpoint_for_device(device_type, context)
                sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
                if setpoint is None or sensor_value is None:
                    self._event_policy.observe(
                        DecisionObservation(
                            location,
                            cluster,
                            device_name,
                            current_time,
                            "auto_pid",
                            sensor_value,
                            setpoint,
                            None,
                            None,
                            control_mode,
                            "control.input_missing",
                            "PID control input is unavailable",
                            device_type,
                            self.ramping_now(device_type, context),
                        )
                    )
                    return None
                output = await self._process_autotune_control(
                    device_type, setpoint, sensor_value, current_time
                )
                error = self._calculate_error(device_type, setpoint, sensor_value)
                observation = DecisionObservation(
                    location,
                    cluster,
                    device_name,
                    current_time,
                    "auto_pid",
                    sensor_value,
                    setpoint,
                    error,
                    output * 100.0,
                    control_mode,
                    "control.auto_pid_decision",
                    "Auto-tuning PID calculated control output",
                    device_type,
                    self.ramping_now(device_type, context),
                )
                context["pid_decision_observation"] = observation
                self._event_policy.observe(observation)
                return output

            # Standard PID control (control_mode == 'pid')
            controller = await self.get_pid_controller(location, cluster, device_name, device_type)
            if not controller:
                return None

            # Get effective setpoint from context
            setpoint = self._get_setpoint_for_device(device_type, context)
            if setpoint is None:
                logger.debug(f"No setpoint available for {device_name} ({device_type})")
                self._event_policy.observe(
                    DecisionObservation(
                        location,
                        cluster,
                        device_name,
                        current_time,
                        "pid",
                        None,
                        None,
                        None,
                        None,
                        control_mode,
                        "control.input_missing",
                        "PID setpoint is unavailable",
                        device_type,
                        self.ramping_now(device_type, context),
                    )
                )
                return None

            # Get sensor value
            sensor_value = self._get_sensor_value_for_device(device_type, sensor_values)
            if sensor_value is None:
                logger.debug(f"No sensor value available for {device_name} ({device_type})")
                self._event_policy.observe(
                    DecisionObservation(
                        location,
                        cluster,
                        device_name,
                        current_time,
                        "pid",
                        None,
                        setpoint,
                        None,
                        None,
                        control_mode,
                        "control.input_missing",
                        "PID sensor value is unavailable",
                        device_type,
                        self.ramping_now(device_type, context),
                    )
                )
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
                observation = DecisionObservation(
                    location,
                    cluster,
                    device_name,
                    current_time,
                    "pid",
                    sensor_value,
                    setpoint,
                    error,
                    output * 100.0,
                    current_mode or control_mode,
                    "control.pid_decision",
                    f"PID calculated control output; Kp={controller.kp:g}, "
                    f"Ki={controller.ki:g}, Kd={controller.kd:g}",
                    device_type,
                    self.ramping_now(device_type, context),
                )
                context["pid_decision_observation"] = observation
                self._event_policy.observe(observation)

                return output

            except Exception as e:
                logger.error(f"PID calculation failed for {device_name} ({device_type}): {e}")
                return None

    @staticmethod
    def ramping_now(device_type: str, context: dict[str, Any]) -> bool:
        """Map each PID device type onto its own ramp-progress domain."""
        ramp_progress = context.get("ramp_progress") or {}
        domain = {"humidifier": "humidity", "dehumidifier": "vpd"}.get(device_type, device_type)
        return ramp_progress.get(domain) is not None

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
