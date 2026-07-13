"""Rule-based device control for non-PID binary actuators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class RulesMixin:
    """Mixin for rule-based control output and sensor mapping."""

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
