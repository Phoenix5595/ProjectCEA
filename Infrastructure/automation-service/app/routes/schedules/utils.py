"""Utility functions for schedule routes."""

from __future__ import annotations

from datetime import time as dt_time
from typing import Any

from fastapi import HTTPException

from app.database import DatabaseManager
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
    # Get room schedule from mode_parameters (room_schedule rows deleted in T10)
    room_schedule = None
    try:
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if active_mode:
            mode_name = active_mode.get("mode_name", "veg")
            submode_name = active_mode.get("submode_name")
            params = await database.room_mode_repo.get_mode_parameters(
                location, cluster, mode_name, submode_name
            )
            if params:
                room_schedule = {
                    "day_start_time": _to_hhmm(params.get("day_start_time")),
                    "day_end_time": _to_hhmm(params.get("night_start_time")),
                    "night_start_time": _to_hhmm(params.get("night_start_time")),
                    "night_end_time": _to_hhmm(params.get("day_start_time")),
                    "ramp_up_duration": params.get("light_ramp_up_minutes", 30) or 30,
                    "ramp_down_duration": params.get("light_ramp_down_minutes", 15) or 15,
                }
    except Exception as e:
        logger.warning(f"Failed to get mode_parameters for {location}/{cluster}: {e}")

    if room_schedule is None:
        room_schedule = {}

    # Get climate periods for setpoints (climate_periods replaced legacy climate schedule)
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

    # Get light targets from light_target_intensity (replaces SUN/DAY schedule rows)
    lights = {}
    try:
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if active_mode:
            mode_id = active_mode.get("mode_id")
            if mode_id is not None:
                intensities = await database.light_target_intensity_repo.get_intensities_for_room(
                    location, cluster, mode_id
                )
                # intensities is {device_id: target_intensity}; need device_name
                pool = await database._get_pool()
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT device_id, device_name FROM device_registry "
                        "WHERE location = $1 AND cluster = $2 AND device_type = 'light'",
                        location,
                        cluster,
                    )
                    id_to_name = {r["device_id"]: r["device_name"] for r in rows}
                    lights = {
                        id_to_name[did]: {"target_intensity": intensity}
                        for did, intensity in intensities.items()
                        if did in id_to_name
                    }
    except Exception as e:
        logger.warning(f"Failed to get light targets for {location}/{cluster}: {e}")

    # Build schedule state structure
    schedule_state = {
        "room": {
            "day_start_time": room_schedule.get("day_start_time", "06:00"),
            "day_end_time": room_schedule.get("day_end_time", "20:00"),
            "night_start_time": room_schedule.get("night_start_time", "20:00"),
            "night_end_time": room_schedule.get("night_end_time", "06:00"),
            "ramp_up_duration": room_schedule.get("ramp_up_duration", 30) or 30,
            "ramp_down_duration": room_schedule.get("ramp_down_duration", 15) or 15,
        },
        "climate": {
            # Legacy pre_day/pre_night fields removed - system now uses climate_periods
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
