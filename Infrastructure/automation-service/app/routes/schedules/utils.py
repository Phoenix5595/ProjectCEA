"""Utility functions for schedule routes."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from fastapi import HTTPException

from shared.infra_logging import get_logger

logger = get_logger(__name__)


def _parse_time_str(value: str) -> dt_time:
    """Parse 'HH:MM' or 'HH:MM:SS' into a datetime.time."""
    parts = value.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    second = int(parts[2]) if len(parts) > 2 else 0
    return dt_time(hour, minute, second)


def _to_hhmm(value: Any) -> str:
    """Convert time object or string to HH:MM format."""
    if value is None:
        return "06:00"
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
    return s[:5] if len(s) >= 5 else "06:00"


def _ensure_light_schedules_are_daily(
    mode: str | None, target_intensity: float | None, day_of_week: int | None
) -> None:
    """Enforce that light schedules remain daily (day_of_week must be NULL).

    A schedule is considered a light schedule when:
    - mode is SUN or DAY (light sun schedule), and
    - target_intensity is provided (ramps only apply to lights)
    """
    if (
        mode
        and mode.upper() in ("SUN", "DAY")
        and target_intensity is not None
        and day_of_week is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="Light schedules must be daily: set day_of_week to null for lights with target_intensity.",
        )
