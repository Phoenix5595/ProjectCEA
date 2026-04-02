"""Build initial per-device control context from effective setpoints (DeviceProcessor phase)."""

from __future__ import annotations

from typing import Any


def build_initial_control_context(
    location: str,
    cluster: str,
    effective_data: dict[str, Any] | None,
    current_mode: str | None,
    previous_climate_mode: str | None,
) -> dict[str, Any]:
    """Shared context dict passed into PID / VPD / light / device_controller paths."""
    if effective_data:
        return {
            "effective_heating_setpoint": effective_data.get("effective_heating_setpoint"),
            "effective_cooling_setpoint": effective_data.get("effective_cooling_setpoint"),
            "effective_humidity_setpoint": effective_data.get("effective_humidity_setpoint"),
            "effective_co2_setpoint": effective_data.get("effective_co2_setpoint"),
            "effective_vpd_setpoint": effective_data.get("effective_vpd_setpoint"),
            "current_vpd": effective_data.get("current_vpd"),
            "failsafe_active": False,  # TODO: implement failsafe logic
            "current_mode": current_mode,
            "previous_climate_mode": {(location, cluster): previous_climate_mode}
            if previous_climate_mode is not None
            else {},
        }
    return {
        "effective_heating_setpoint": None,
        "effective_cooling_setpoint": None,
        "effective_humidity_setpoint": None,
        "effective_co2_setpoint": None,
        "effective_vpd_setpoint": None,
        "current_vpd": None,
        "failsafe_active": False,
        "current_mode": current_mode,
        "previous_climate_mode": {},
    }
