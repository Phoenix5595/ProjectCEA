"""Build initial per-device control context from effective setpoints (DeviceProcessor phase)."""

from __future__ import annotations

from typing import Any


def build_initial_control_context(
    location: str,
    cluster: str,
    effective_data: dict[str, Any] | None,
    current_mode: str | None,
    previous_climate_mode: str | None,
    failsafe_active: bool = False,
) -> dict[str, Any]:
    """Shared context dict passed into PID / VPD / light / device_controller paths.

    ``failsafe_active`` is the authoritative crop-safety flag for this tick. When True,
    downstream paths short-circuit: ``device_controller._determine_control_mode`` returns
    ``"failsafe"`` (no PID output, relays commanded off by later logic) and
    ``LightAuthorityResolver.resolve`` forces lights to 0% with authority=``safety``.
    The caller is responsible for deciding the value (feature-flag + Redis read).
    """
    if effective_data:
        return {
            "effective_heating_setpoint": effective_data.get("effective_heating_setpoint"),
            "effective_cooling_setpoint": effective_data.get("effective_cooling_setpoint"),
            "effective_humidity_setpoint": effective_data.get("effective_humidity_setpoint"),
            "effective_co2_setpoint": effective_data.get("effective_co2_setpoint"),
            "effective_vpd_setpoint": effective_data.get("effective_vpd_setpoint"),
            "current_vpd": effective_data.get("current_vpd"),
            "failsafe_active": failsafe_active,
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
        "failsafe_active": failsafe_active,
        "current_mode": current_mode,
        "previous_climate_mode": {},
    }
