"""Utility functions for schedule routes."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from fastapi import HTTPException

from app.database import DatabaseManager


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


async def _build_schedule_state(
    database: DatabaseManager, location: str, cluster: str
) -> dict[str, Any]:
    """Build complete schedule state from database following canonical schema.

    Args:
        database: Database manager
        location: Location name
        cluster: Cluster name

    Returns:
        Complete schedule state matching canonical schema
    """
    # Get room schedule
    room_schedule = await database.schedule_repo.get_room_schedule(location, cluster)

    # Get climate schedule
    climate_schedule = await database.schedule_repo.get_climate_schedule(location, cluster)

    # Get climate periods for setpoints
    periods = await database.climate_periods_repo.get_periods(location, cluster)
    periods_data = []
    for period in periods:
        periods_data.append(
            {
                "period_name": period.get("period_name"),
                "start_time": str(period.get("start_time")) if period.get("start_time") else None,
                "end_time": str(period.get("end_time")) if period.get("end_time") else None,
                "ramp_minutes": period.get("ramp_minutes", 0) or 0,
                "heating_setpoint": period.get("heating_setpoint"),
                "cooling_setpoint": period.get("cooling_setpoint"),
                "vpd_setpoint": period.get("vpd_setpoint"),
                "co2_setpoint": period.get("co2_setpoint"),
            }
        )

    # Get light schedules to extract target_intensity
    all_schedules = await database.schedule_repo.get_schedules(location, cluster)
    lights = {}
    for sched in all_schedules:
        device_name = sched.get("device_name", "")
        if (
            device_name.startswith("light_")
            and sched.get("mode") in ("SUN", "DAY")
            and sched.get("enabled")
        ):
            target_intensity = sched.get("target_intensity")
            if target_intensity is not None:
                lights[device_name] = {"target_intensity": float(target_intensity)}

    # Build schedule state structure
    room_data = room_schedule or {
        "day_start_time": "06:00",
        "day_end_time": "20:00",
        "night_start_time": "20:00",
        "night_end_time": "06:00",
        "ramp_up_duration": 30,
        "ramp_down_duration": 15,
    }

    schedule_state = {
        "room": {
            "day_start_time": room_data.get("day_start_time", "06:00"),
            "day_end_time": room_data.get("day_end_time", "20:00"),
            "night_start_time": room_data.get("night_start_time", "20:00"),
            "night_end_time": room_data.get("night_end_time", "06:00"),
            "ramp_up_duration": room_data.get("ramp_up_duration", 30) or 30,
            "ramp_down_duration": room_data.get("ramp_down_duration", 15) or 15,
        },
        "climate": {
            "pre_day_duration": climate_schedule.get("pre_day_duration", 0)
            if climate_schedule
            else 0,
            "pre_night_duration": climate_schedule.get("pre_night_duration", 0)
            if climate_schedule
            else 0,
        },
        "periods": periods_data,
        "lights": lights,
    }

    return schedule_state


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
