"""Shared Pydantic validation for PID gain limits."""

from __future__ import annotations

from typing import Any

from pydantic import RootModel, model_validator


class PidLimits(RootModel[dict[str, Any]]):
    """Validate the existing ``control.pid_limits`` shape and bounds."""

    @model_validator(mode="before")
    @classmethod
    def validate_limits(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        for device_type, limits in value.items():
            if not isinstance(limits, dict):
                continue
            for parameter in ("kp", "ki", "kd"):
                min_key = f"{parameter}_min"
                max_key = f"{parameter}_max"
                min_value = limits.get(min_key)
                max_value = limits.get(max_key)
                if isinstance(min_value, int | float) and min_value < 0:
                    raise ValueError(
                        f"control.pid_limits.{device_type}.{min_key} must be non-negative"
                    )
                if isinstance(max_value, int | float) and max_value < 0:
                    raise ValueError(
                        f"control.pid_limits.{device_type}.{max_key} must be non-negative"
                    )
                if (
                    isinstance(min_value, int | float)
                    and isinstance(max_value, int | float)
                    and min_value > max_value
                ):
                    raise ValueError(
                        f"control.pid_limits.{device_type}.{min_key} must be <= {max_key}"
                    )
        return value


__all__ = ["PidLimits"]
