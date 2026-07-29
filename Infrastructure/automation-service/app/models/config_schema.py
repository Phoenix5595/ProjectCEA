from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class AppConfig(BaseModel):
    """Validate YAML-owned hardware, control, and sensor metadata only."""

    hardware: dict[str, Any] | None = None
    automation: dict[str, Any] | None = None
    control: dict[str, Any] | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def validate_structure(cls, values: dict[str, Any]) -> dict[str, Any]:
        hardware = values.get("hardware", {}) or {}
        control = values.get("control", {}) or {}

        for key in ("i2c_bus", "mcp_i2c_bus", "dfr0971_i2c_bus"):
            bus = hardware.get(key)
            if bus is not None and isinstance(bus, int) and not 0 <= bus <= 7:
                raise ValueError(f"hardware.{key} must be between 0 and 7 (got {bus})")

        update_interval = control.get("update_interval")
        if update_interval is not None and isinstance(update_interval, (int, float)):
            if not 1 <= update_interval <= 5:
                raise ValueError(
                    "control.update_interval must be between 1 and 5 seconds "
                    f"(got {update_interval})"
                )

        sensors = values.get("sensors")
        if isinstance(sensors, dict):
            flower_sensors = sensors.get("Flower Room")
            if isinstance(flower_sensors, dict):
                missing_clusters = [
                    cluster for cluster in ("front", "back") if cluster not in flower_sensors
                ]
                if missing_clusters:
                    raise ValueError(
                        "Flower Room sensors must define both 'front' and 'back' clusters "
                        f"(missing: {', '.join(missing_clusters)})"
                    )

        pid_limits = control.get("pid_limits")
        if isinstance(pid_limits, dict):
            for device_type, limits in pid_limits.items():
                if not isinstance(limits, dict):
                    continue
                for parameter in ("kp", "ki", "kd"):
                    min_key = f"{parameter}_min"
                    max_key = f"{parameter}_max"
                    min_value = limits.get(min_key)
                    max_value = limits.get(max_key)
                    if isinstance(min_value, (int, float)) and min_value < 0:
                        raise ValueError(
                            f"control.pid_limits.{device_type}.{min_key} must be non-negative"
                        )
                    if isinstance(max_value, (int, float)) and max_value < 0:
                        raise ValueError(
                            f"control.pid_limits.{device_type}.{max_key} must be non-negative"
                        )
                    if (
                        isinstance(min_value, (int, float))
                        and isinstance(max_value, (int, float))
                        and min_value > max_value
                    ):
                        raise ValueError(
                            f"control.pid_limits.{device_type}.{min_key} must be <= {max_key}"
                        )

        return values
