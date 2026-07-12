"""Room schedule endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.schemas.schedules import RoomScheduleCreate
from shared.infra_logging import get_logger

from .base import (
    get_config,
    get_database,
)

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
    devices = await config.get_devices()
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
    """Return photoperiod and ramp times from mode_parameters for the active mode."""
    try:
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if not active_mode:
            return {
                "day_start_time": "06:00",
                "day_end_time": "20:00",
                "night_start_time": "20:00",
                "night_end_time": "06:00",
                "ramp_up_duration": 30,
                "ramp_down_duration": 15,
            }

        mode_name = active_mode.get("mode_name", "veg")
        submode_name = active_mode.get("submode_name")
        params = await database.room_mode_repo.get_mode_parameters(
            location, cluster, mode_name, submode_name
        )

        if not params:
            return {
                "day_start_time": "06:00",
                "day_end_time": "20:00",
                "night_start_time": "20:00",
                "night_end_time": "06:00",
                "ramp_up_duration": 30,
                "ramp_down_duration": 15,
            }

        day_start = _to_hhmm(params.get("day_start_time"))
        night_start = _to_hhmm(params.get("night_start_time"))

        return {
            "day_start_time": day_start,
            "day_end_time": night_start,
            "night_start_time": night_start,
            "night_end_time": day_start,
            "ramp_up_duration": params.get("light_ramp_up_minutes", 30) or 30,
            "ramp_down_duration": params.get("light_ramp_down_minutes", 15) or 15,
        }
    except Exception as e:
        logger.warning(f"Error retrieving room schedule from mode_parameters: {e}")
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "night_start_time": "20:00",
            "night_end_time": "06:00",
            "ramp_up_duration": 30,
            "ramp_down_duration": 15,
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

    devices = await config.get_devices()
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

    # Resolve active mode for mode_parameters update
    active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
    mode_name = str(active_mode.get("mode_name", "veg")) if active_mode else "veg"
    submode_name = active_mode.get("submode_name") if active_mode else None

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

                    if filtered_ids:
                        await database.schedule_repo.delete_schedules_bulk(
                            filtered_ids, cast("Connection", conn)
                        )
                        logger.info(
                            f"Deleted {len(filtered_ids)} existing schedules for {location}/{cluster}"
                        )

                for device_name, device_info in room_devices.items():
                    device_type = device_info.get("device_type", "")
                    display_name = device_info.get("display_name", device_name)

                    # Only create DAY/NIGHT rows for non-light devices
                    if device_type == "light":
                        continue

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

                # Update mode_parameters directly with photoperiod times and ramp durations
                current_params = await database.room_mode_repo.get_mode_parameters(
                    location, cluster, mode_name, submode_name
                )
                if not current_params:
                    current_params = {}
                merged_params = {
                    **current_params,
                    "day_start_time": schedule.day_start_time,
                    "night_start_time": schedule.night_start_time,
                    "light_ramp_up_minutes": schedule.ramp_up_duration or 30,
                    "light_ramp_down_minutes": schedule.ramp_down_duration or 15,
                }
                await database.room_mode_repo.save_mode_parameters(
                    location, cluster, mode_name, submode_name, merged_params
                )

                logger.info(
                    f"Successfully created {schedules_created} non-light schedules for {location}/{cluster} in transaction"
                )
    except Exception as e:
        logger.error(f"Error saving room schedule for {location}/{cluster}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}") from e

    # Publish MODE_CHANGED event AFTER transaction commits so consumers read committed data
    try:
        from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus

        event_bus = get_event_bus()
        event = ConfigChangeEvent(
            event_type=ConfigEventType.MODE_CHANGED,
            location=location,
            cluster=cluster,
            config_type="mode_parameters",
            data={
                "action": "room_schedule_saved",
                "schedules_created": schedules_created,
                "day_start_time": schedule.day_start_time,
                "night_start_time": schedule.night_start_time,
                "ramp_up_duration": schedule.ramp_up_duration,
                "ramp_down_duration": schedule.ramp_down_duration,
            },
        )
        await event_bus.publish(event)
        logger.info(
            f"Published MODE_CHANGED event for {location}/{cluster} after transaction commit"
        )

        # Invalidate cache
        from app.state import get_state_manager

        state = get_state_manager()
        await state.delete(f"schedules:loc:{location}:cluster:{cluster}")
        await state.delete(f"schedules:loc:{location}:cluster:{cluster}:climate")
        await state.delete("schedules:all")
    except Exception as e:
        logger.warning(f"Failed to publish mode event or invalidate cache: {e}")

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
