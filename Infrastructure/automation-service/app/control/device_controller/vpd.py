"""VPD-related device control for humidifier, dehumidifier, and exhaust."""

from __future__ import annotations

from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class VPDCalculatorMixin:
    """Mixin for VPD-based and failsafe control output calculations."""

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
