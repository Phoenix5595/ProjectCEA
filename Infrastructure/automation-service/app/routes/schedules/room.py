"""Room schedule endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.schemas.schedules import RoomScheduleCreate
from shared.infra_logging import get_logger

from .base import (
    get_automation_redis,
    get_config,
    get_database,
)
from .utils import _build_schedule_state

if TYPE_CHECKING:
    from asyncpg import Connection

logger = get_logger(__name__)

router = APIRouter()


def _to_hhmm(value: Any) -> str:
    if value is None:
        return "06:00"
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
    return s[:5] if len(s) >= 5 else "06:00"


@router.post("/api/room-schedule/sync-all-from-mode-parameters")
async def sync_all_room_schedules_from_mode_parameters(
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    devices = config.get_devices()
    results: list[dict[str, Any]] = []
    for location, clusters in (devices or {}).items():
        if not isinstance(clusters, dict):
            continue
        for cluster in clusters:
            try:
                out = await sync_room_schedule_from_mode_parameters(
                    location, cluster, database, config
                )
                results.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "success": True,
                        "schedules_created": out.get("schedules_created", 0),
                        "devices_configured": out.get("devices_configured", 0),
                    }
                )
            except HTTPException as e:
                results.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "success": False,
                        "error": e.detail,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "success": False,
                        "error": str(e),
                    }
                )
    return {"synced": results}


@router.get("/api/room-schedule/{location}/{cluster}")
async def get_room_schedule(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    pool = await database._get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT start_time, end_time, ramp_up_duration, ramp_down_duration
                FROM schedules
                WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                ORDER BY created_at DESC
                LIMIT 1
            """,
                location,
                cluster,
            )

            if row:

                def format_time(time_val):
                    if isinstance(time_val, str):
                        return time_val
                    if hasattr(time_val, "hour") and hasattr(time_val, "minute"):
                        return f"{time_val.hour:02d}:{time_val.minute:02d}"
                    return str(time_val)

                day_start = format_time(row["start_time"])
                day_end = format_time(row["end_time"])
                night_start = day_end
                night_end = day_start

                return {
                    "day_start_time": day_start,
                    "day_end_time": day_end,
                    "night_start_time": night_start,
                    "night_end_time": night_end,
                    "ramp_up_duration": row["ramp_up_duration"]
                    if row["ramp_up_duration"] is not None
                    else 30,
                    "ramp_down_duration": row["ramp_down_duration"]
                    if row["ramp_down_duration"] is not None
                    else 15,
                }
    except Exception as e:
        logger.warning(
            f"Error retrieving room schedule from database: {e}. Falling back to inferring from schedules."
        )

    all_schedules = await database.schedule_repo.get_schedules(location, cluster)
    schedules = [
        s for s in all_schedules if s.get("device_name") not in ["room_schedule", "climate"]
    ]

    if not schedules:
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "night_start_time": "20:00",
            "night_end_time": "06:00",
            "ramp_up_duration": 30,
            "ramp_down_duration": 15,
        }

    day_schedule = None
    night_schedule = None

    for schedule in schedules:
        target_intensity = schedule.get("target_intensity")
        mode = (schedule.get("mode") or "").upper()

        if mode in ("SUN", "DAY") or (target_intensity is not None and target_intensity > 0):
            if day_schedule is None or mode in ("SUN", "DAY"):
                day_schedule = schedule
        elif mode in ("MOON", "NIGHT") or (target_intensity is not None and target_intensity == 0):
            if night_schedule is None or mode in ("MOON", "NIGHT"):
                night_schedule = schedule

    def format_time_value(time_val, default: str) -> str:
        if time_val is None:
            return default
        if isinstance(time_val, str):
            return time_val
        if hasattr(time_val, "hour") and hasattr(time_val, "minute"):
            return f"{time_val.hour:02d}:{time_val.minute:02d}"
        return str(time_val)

    def parse_time_to_minutes(time_str: str) -> int:
        try:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError, AttributeError) as e:
            logger.warning(f"Invalid time format '{time_str}': {e}, returning 0")
            return 0

    if day_schedule:
        day_start_raw = format_time_value(day_schedule.get("start_time"), "06:00")
        day_end_raw = format_time_value(day_schedule.get("end_time"), "20:00")
        parse_time_to_minutes(day_start_raw)
        parse_time_to_minutes(day_end_raw)
        day_start = day_start_raw
        day_end = day_end_raw
    else:
        day_start = "06:00"
        day_end = "20:00"

    if night_schedule:
        night_start_raw = format_time_value(night_schedule.get("start_time"), "20:00")
        night_end_raw = format_time_value(night_schedule.get("end_time"), "06:00")
        night_start = night_start_raw
        night_end = night_end_raw
    else:
        if day_schedule:
            night_start = day_end
            night_end = day_start
        else:
            night_start = "20:00"
            night_end = "06:00"

    ramp_up = None
    ramp_down = None

    if day_schedule:
        ramp_up = day_schedule.get("ramp_up_duration")
        ramp_down = day_schedule.get("ramp_down_duration")

    if ramp_up is None:
        ramp_up = 30
    if ramp_down is None:
        if night_schedule:
            ramp_down = night_schedule.get("ramp_down_duration", 15)
        else:
            ramp_down = 15

    return {
        "day_start_time": str(day_start),
        "day_end_time": str(day_end),
        "night_start_time": str(night_start),
        "night_end_time": str(night_end),
        "ramp_up_duration": ramp_up,
        "ramp_down_duration": ramp_down,
    }


@router.post("/api/room-schedule/{location}/{cluster}")
async def save_room_schedule(
    location: str,
    cluster: str,
    schedule: RoomScheduleCreate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    try:
        from datetime import time as dt_time

        day_start_parts = schedule.day_start_time.split(":")
        day_end_parts = schedule.day_end_time.split(":")
        night_start_parts = schedule.night_start_time.split(":")
        night_end_parts = schedule.night_end_time.split(":")

        dt_time(int(day_start_parts[0]), int(day_start_parts[1]))
        dt_time(int(day_end_parts[0]), int(day_end_parts[1]))
        dt_time(int(night_start_parts[0]), int(night_start_parts[1]))
        dt_time(int(night_end_parts[0]), int(night_end_parts[1]))
    except (ValueError, IndexError) as e:
        raise HTTPException(
            status_code=400, detail=f"Invalid time format. Use HH:MM format. Error: {e}"
        ) from e

    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_up_duration must be >= 0")
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(status_code=400, detail="ramp_down_duration must be >= 0")

    if schedule.day_end_time != schedule.night_start_time:
        raise HTTPException(
            status_code=400,
            detail=f"day_end_time ({schedule.day_end_time}) must equal night_start_time ({schedule.night_start_time})",
        )
    if schedule.day_start_time != schedule.night_end_time:
        raise HTTPException(
            status_code=400,
            detail=f"day_start_time ({schedule.day_start_time}) must equal night_end_time ({schedule.night_end_time})",
        )

    devices = config.get_devices()
    room_devices = devices.get(location, {}).get(cluster, {})

    if not room_devices:
        raise HTTPException(status_code=404, detail=f"No devices found for {location}/{cluster}")

    existing_schedules = await database.schedule_repo.get_schedules(location, cluster)
    schedule_ids_to_delete = [
        s["id"]
        for s in existing_schedules
        if s.get("id") and s.get("device_name") not in ["room_schedule", "climate"]
    ]

    pool = await database._get_pool()
    schedules_created = 0
    preserved_intensities: dict[str, float] = {}

    # Per-device SUN ramps: use active mode's light_ramp_* (never unscoped mode_parameters LIMIT 1).
    light_ramp_up = 15
    light_ramp_down = 15
    active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
    mode_params_for_ramps: dict[str, Any] | None = None
    if active_mode:
        mode_params_for_ramps = await database.room_mode_repo.get_mode_parameters(
            location,
            cluster,
            str(active_mode.get("mode_name", "veg")),
            active_mode.get("submode_name"),
        )
    if mode_params_for_ramps:
        light_ramp_up = int(mode_params_for_ramps.get("light_ramp_up_minutes") or 15)
        light_ramp_down = int(mode_params_for_ramps.get("light_ramp_down_minutes") or 15)
    elif schedule.ramp_up_duration is not None or schedule.ramp_down_duration is not None:
        if schedule.ramp_up_duration is not None:
            light_ramp_up = int(schedule.ramp_up_duration)
        if schedule.ramp_down_duration is not None:
            light_ramp_down = int(schedule.ramp_down_duration)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                if schedule_ids_to_delete:
                    room_schedule_ids = await conn.fetch(
                        """
                        SELECT id FROM schedules
                        WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                    """,
                        location,
                        cluster,
                    )
                    room_schedule_id_set = {r["id"] for r in room_schedule_ids}
                    filtered_ids = [
                        sid for sid in schedule_ids_to_delete if sid not in room_schedule_id_set
                    ]

                    if room_devices:
                        for device_name, device_info in room_devices.items():
                            if device_info.get("device_type") == "light" and device_info.get(
                                "dimming_enabled"
                            ):
                                existing_day = await conn.fetchrow(
                                    """
                                    SELECT target_intensity
                                    FROM schedules
                                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                                      AND mode IN ('SUN', 'DAY') AND enabled = TRUE
                                    ORDER BY updated_at DESC
                                    LIMIT 1
                                """,
                                    location,
                                    cluster,
                                    device_name,
                                )
                                if existing_day and existing_day["target_intensity"] is not None:
                                    preserved_intensities[device_name] = existing_day[
                                        "target_intensity"
                                    ]

                    if filtered_ids:
                        await database.schedule_repo.delete_schedules_bulk(
                            filtered_ids, cast("Connection", conn)
                        )
                        logger.info(
                            f"Deleted {len(filtered_ids)} existing schedules for {location}/{cluster}"
                        )

                from datetime import time as dt_time

                day_start_parts = schedule.day_start_time.split(":")
                day_start_time_obj = dt_time(int(day_start_parts[0]), int(day_start_parts[1]))
                day_end_parts = schedule.day_end_time.split(":")
                day_end_time_obj = dt_time(int(day_end_parts[0]), int(day_end_parts[1]))

                existing_room_schedule = await conn.fetchrow(
                    """
                    SELECT id FROM schedules
                    WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                    LIMIT 1
                """,
                    location,
                    cluster,
                )

                if existing_room_schedule:
                    await conn.execute(
                        """
                        UPDATE schedules
                        SET start_time = $1, end_time = $2,
                            ramp_up_duration = $3, ramp_down_duration = $4,
                            created_at = NOW()
                        WHERE id = $5
                    """,
                        day_start_time_obj,
                        day_end_time_obj,
                        schedule.ramp_up_duration,
                        schedule.ramp_down_duration,
                        existing_room_schedule["id"],
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time, enabled, ramp_up_duration, ramp_down_duration)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                        f"Room Schedule {location}/{cluster}",
                        location,
                        cluster,
                        "room_schedule",
                        day_start_time_obj,
                        day_end_time_obj,
                        True,
                        schedule.ramp_up_duration,
                        schedule.ramp_down_duration,
                    )

                for device_name, device_info in room_devices.items():
                    device_type = device_info.get("device_type", "")
                    dimming_enabled = device_info.get("dimming_enabled", False)
                    display_name = device_info.get("display_name", device_name)

                    if device_type == "light" and dimming_enabled:
                        target_intensity = preserved_intensities.get(device_name, 100)

                        day_schedule_id = await database.schedule_repo.create_schedule(
                            name=f"{display_name} - Sun",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.day_start_time,
                            end_time=schedule.day_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode="SUN",
                            target_intensity=target_intensity,
                            ramp_up_duration=light_ramp_up,
                            ramp_down_duration=light_ramp_down,
                            conn=cast(Any, conn),
                        )
                        if day_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create sun schedule for {device_name}")

                        night_schedule_id = await database.schedule_repo.create_schedule(
                            name=f"{display_name} - Moon",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.night_start_time,
                            end_time=schedule.night_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode="MOON",
                            target_intensity=0,
                            ramp_up_duration=None,
                            ramp_down_duration=None,
                            conn=cast(Any, conn),
                        )
                        if night_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create moon schedule for {device_name}")
                    else:
                        day_schedule_id = await database.schedule_repo.create_schedule(
                            name=f"{display_name} - Day",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.day_start_time,
                            end_time=schedule.day_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode="DAY",
                            target_intensity=None,
                            ramp_up_duration=None,
                            ramp_down_duration=None,
                            conn=cast(Any, conn),
                        )
                        if day_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create day schedule for {device_name}")

                        night_schedule_id = await database.schedule_repo.create_schedule(
                            name=f"{display_name} - Night",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.night_start_time,
                            end_time=schedule.night_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode="NIGHT",
                            target_intensity=None,
                            ramp_up_duration=None,
                            ramp_down_duration=None,
                            conn=cast(Any, conn),
                        )
                        if night_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create night schedule for {device_name}")

                logger.info(
                    f"Successfully created {schedules_created} schedules for {location}/{cluster} in transaction"
                )
    except Exception as e:
        logger.error(f"Error saving room schedule for {location}/{cluster}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}") from e

    # Publish events AFTER transaction commits so consumers read committed data
    try:
        from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus

        event_bus = get_event_bus()
        event = ConfigChangeEvent(
            event_type=ConfigEventType.SCHEDULE_CHANGED,
            location=location,
            cluster=cluster,
            config_type="schedules",
            data={"action": "room_schedule_saved", "schedules_created": schedules_created},
        )
        await event_bus.publish(event)
        logger.info(
            f"Published SCHEDULE_CHANGED event for {location}/{cluster} after transaction commit"
        )

        # Invalidate cache
        from app.state import get_state_manager

        state = get_state_manager()
        await state.delete(f"schedules:loc:{location}:cluster:{cluster}")
        await state.delete(f"schedules:loc:{location}:cluster:{cluster}:climate")
        await state.delete("schedules:all")
    except Exception as e:
        logger.warning(f"Failed to publish schedule event or invalidate cache: {e}")

    await database.config_repo.log_config_version(
        config_type="room_schedule",
        author="system",
        comment=f"Room schedule updated for {location}/{cluster}",
        location=location,
        cluster=cluster,
        changes={
            "day_start_time": schedule.day_start_time,
            "day_end_time": schedule.day_end_time,
            "night_start_time": schedule.night_start_time,
            "night_end_time": schedule.night_end_time,
            "ramp_up_duration": schedule.ramp_up_duration,
            "ramp_down_duration": schedule.ramp_down_duration,
            "schedules_created": schedules_created,
            "devices_configured": len(room_devices),
        },
    )

    try:
        from app.routes.websocket import broadcast_room_schedule_update

        await broadcast_room_schedule_update(
            location,
            cluster,
            {
                "day_start_time": schedule.day_start_time,
                "day_end_time": schedule.day_end_time,
                "night_start_time": schedule.night_start_time,
                "night_end_time": schedule.night_end_time,
                "ramp_up_duration": schedule.ramp_up_duration,
                "ramp_down_duration": schedule.ramp_down_duration,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to broadcast room schedule update: {e}")

    try:
        redis_client = get_automation_redis()
        if redis_client:
            schedule_state = await _build_schedule_state(database, location, cluster)
            redis_client.write_schedule_state(location, cluster, schedule_state)
            logger.info(f"Wrote schedule state to Redis for {location}/{cluster}")
    except Exception as e:
        logger.warning(f"Failed to write schedule state to Redis: {e}")

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "schedules_created": schedules_created,
        "devices_configured": len(room_devices),
    }


@router.post("/api/room-schedule/{location}/{cluster}/sync-from-mode-parameters")
async def sync_room_schedule_from_mode_parameters(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    active = await database.room_mode_repo.get_active_mode(location, cluster)
    if not active:
        raise HTTPException(
            status_code=404,
            detail=f"No active mode for {location}/{cluster}. Set mode first.",
        )
    mode_name = active.get("mode_name", "veg")
    submode_name = active.get("submode_name")
    params = await database.room_mode_repo.get_mode_parameters(
        location, cluster, mode_name, submode_name
    )
    if not params:
        raise HTTPException(
            status_code=404,
            detail=f"No mode parameters for {location}/{cluster} mode={mode_name}",
        )
    day_start = _to_hhmm(params.get("day_start_time"))
    night_start = _to_hhmm(params.get("night_start_time"))
    schedule = RoomScheduleCreate(
        day_start_time=day_start,
        day_end_time=night_start,
        night_start_time=night_start,
        night_end_time=day_start,
        ramp_up_duration=params.get("light_ramp_up_minutes"),
        ramp_down_duration=params.get("light_ramp_down_minutes"),
    )
    return await save_room_schedule(location, cluster, schedule, database, config)
