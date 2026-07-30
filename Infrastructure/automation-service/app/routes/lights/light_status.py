"""Light status/query endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException

from app.config import ConfigLoader
from app.control.schedule_merge import merge_schedules_with_config
from app.control.scheduler import LOCAL_TZ
from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager
from app.routes.lights import (
    get_config,
    get_database,
    get_dfr0971_manager,
    get_scheduler,
    logger,
    router,
)
from app.schemas.lights import ScheduleTimeControl
from shared.room_light_authority import is_moon_authority_mode


def _schedule_row_active_for_device(
    row: dict[str, Any],
    device_name: str,
    now: datetime,
    scheduler: Any | None,
) -> bool:
    """True if this schedule row applies to ``now`` for the device (weekday + [start,end) like Scheduler)."""
    if row.get("device_name") != device_name:
        return False
    if not row.get("enabled", True):
        return False
    dow = row.get("day_of_week")
    if dow is not None and dow != now.weekday():
        return False
    st = row.get("start_time")
    et = row.get("end_time")
    try:
        st_t = scheduler._parse_time(st) if scheduler else None
        et_t = scheduler._parse_time(et) if scheduler else None
    except (ValueError, TypeError) as e:
        logger.error(f"Failed to parse schedule time st={st} et={et}: {e}", exc_info=True)
        return False
    if not st_t or not et_t:
        return False
    t = now.time()
    if st_t > et_t:
        return t >= st_t or t < et_t
    return st_t <= t < et_t


def _read_light_status_payload(
    location: str,
    cluster: str,
    device_name: str,
    device_info: Mapping[str, Any],
    dfr0971_manager: DFR0971Manager,
    database: DatabaseManager | None,
    scheduler: Any | None,
) -> dict[str, Any] | None:
    """Read Redis/driver intensity and scheduler nominal target. Assumes dimming is configured."""
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")
    if board_id is None or channel is None:
        return None

    intensity = None
    voltage = None

    if database and database._automation_redis:
        redis_data = database._automation_redis.read_light_intensity(location, cluster, device_name)
        if redis_data:
            intensity = redis_data.get("intensity")
            voltage = redis_data.get("voltage")

    if intensity is None or voltage is None:
        intensity = dfr0971_manager.get_intensity(board_id, channel)
        voltage = dfr0971_manager.get_voltage(board_id, channel)

    if intensity is None or voltage is None:
        logger.warning(
            f"Failed to read light hardware state for {device_name} ({location}/{cluster})"
        )
        return None

    boards = dfr0971_manager.list_boards()
    board_info = next((b for b in boards if b["board_id"] == board_id), None)

    target_intensity = None
    if scheduler:
        intensity_details = scheduler.get_light_intensity_details(
            location, cluster, device_name, datetime.now(tz=LOCAL_TZ), intensity
        )
        if intensity_details:
            target_intensity = intensity_details.get("nominal_intensity")

    return {
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel,
        "board_info": board_info,
        # Nominal from the active schedule row (same as scheduler nominal); POST /target updates DB SUN row.
        "target_intensity": target_intensity,
        "scheduler_nominal_intensity": target_intensity,
    }


@router.get("/api/lights/{location}/{cluster}/zone-status")
async def get_zone_lights_status(
    location: str,
    cluster: str,
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
    config: ConfigLoader = Depends(get_config),
    database: DatabaseManager = Depends(get_database),
    scheduler: Any = Depends(get_scheduler),
) -> dict[str, Any]:
    """Return intensity + targets for all dimmable lights in one round-trip (ZoneConfig Light intensity)."""
    devices = await config.get_devices()
    raw = devices.get(location, {}).get(cluster, {}) or {}
    device_entries = [
        (cluster, name, info) for name, info in raw.items() if isinstance(info, Mapping)
    ]
    if not device_entries:
        return {"lights": []}

    now = datetime.now(tz=LOCAL_TZ)
    moon_authority_mode = False
    if database:
        try:
            active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
            moon_authority_mode = is_moon_authority_mode((active_mode or {}).get("mode_name"))
        except Exception as e:
            logger.error(
                f"Failed to look up moon authority mode for {location}/{cluster}: {e}",
                exc_info=True,
            )
            # Zone status is best-effort; a mode lookup miss should not hide light rows.
            moon_authority_mode = False

    # Build device_name -> target_intensity map from light_target_intensity for active mode
    light_targets: dict[str, float] = {}
    if database:
        try:
            active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
            if active_mode:
                mode_id = active_mode.get("mode_id")
                if mode_id is not None:
                    pool = await database._get_pool()
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            """SELECT dr.device_name, lti.target_intensity
                               FROM light_target_intensity lti
                               JOIN device_registry dr ON dr.device_id = lti.device_id
                               WHERE dr.location = $1 AND dr.cluster = $2 AND lti.mode_id = $3""",
                            location,
                            cluster,
                            mode_id,
                        )
                        light_targets = {
                            r["device_name"]: float(r["target_intensity"]) for r in rows
                        }
        except Exception as e:
            logger.error(f"Failed to load light targets for {location}/{cluster}: {e}")

    lights_out: list[dict[str, Any]] = []
    for src_cluster, device_name, device_info in device_entries:
        if device_info.get("device_type") != "light":
            continue
        if not device_info.get("dimming_enabled", False):
            continue
        board_id = device_info.get("dimming_board_id")
        channel = device_info.get("dimming_channel")
        # Channel 0 is valid; only skip when actually missing from config
        if board_id is None or channel is None:
            continue

        payload = _read_light_status_payload(
            location,
            src_cluster,
            device_name,
            device_info,
            dfr0971_manager,
            database,
            scheduler,
        )
        if not payload:
            # Still list the device so the UI can show sliders (same as per-device /status when read fails)
            target_fallback = None
            if scheduler:
                intensity_details = scheduler.get_light_intensity_details(
                    location, cluster, device_name, now, 0.0
                )
                if intensity_details:
                    target_fallback = intensity_details.get("nominal_intensity")
            boards = dfr0971_manager.list_boards()
            board_info = next((b for b in boards if b["board_id"] == board_id), None)
            payload = {
                "location": location,
                "cluster": cluster,
                "device": device_name,
                "intensity": 0.0,
                "voltage": 0.0,
                "board_id": board_id,
                "channel": channel,
                "board_info": board_info,
                "target_intensity": target_fallback,
                "scheduler_nominal_intensity": target_fallback,
            }

        scheduler_effective_intensity: float | None = None
        scheduler_nominal_intensity: float | None = None
        scheduler_is_in_photoperiod: bool | None = None
        if scheduler:
            try:
                det = scheduler.get_light_intensity_details(
                    location,
                    cluster,
                    device_name,
                    now,
                    float(payload.get("intensity") or 0.0),
                )
                if det is not None:
                    scheduler_effective_intensity = float(det.get("effective_intensity"))
                    scheduler_nominal_intensity = float(det.get("nominal_intensity"))
                scheduler_is_in_photoperiod = bool(
                    scheduler.is_in_photoperiod(location, cluster, now)
                )
            except Exception as e:
                logger.error(
                    f"Failed to get scheduler light intensity details for {location}/{cluster}/{device_name}: {e}",
                    exc_info=True,
                )
                # Zone status should remain best-effort even if scheduler details fail.

        if moon_authority_mode:
            scheduler_effective_intensity = 0.0
            scheduler_nominal_intensity = 0.0
            scheduler_is_in_photoperiod = False

        # Derive active schedule mode from photoperiod state (replaces schedules_list lookup)
        active_schedule_mode: str | None = None
        if scheduler_is_in_photoperiod is not None:
            active_schedule_mode = "SUN" if scheduler_is_in_photoperiod else "MOON"
        if moon_authority_mode:
            active_schedule_mode = "MOON"

        # Day target from light_target_intensity (replaces SUN/DAY schedule row lookup)
        day_target_intensity = light_targets.get(device_name)
        if day_target_intensity is None and payload.get("target_intensity") is not None:
            day_target_intensity = float(payload["target_intensity"])

        lights_out.append(
            {
                **payload,
                "display_name": device_info.get("display_name"),
                "day_target_intensity": day_target_intensity,
                "schedule_sun_target_intensity": day_target_intensity,
                "scheduler_effective_intensity": scheduler_effective_intensity,
                "scheduler_nominal_intensity": scheduler_nominal_intensity,
                "scheduler_is_in_photoperiod": scheduler_is_in_photoperiod,
                "active_schedule_mode": active_schedule_mode,
            }
        )

    return {"lights": lights_out}


@router.get("/api/lights/{location}/{cluster}/{device_name}/status")
async def get_light_status(
    location: str,
    cluster: str,
    device_name: str,
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
    config: ConfigLoader = Depends(get_config),
    database: DatabaseManager = Depends(get_database),
    scheduler: Any = Depends(get_scheduler),
) -> dict[str, Any]:
    """Get current light status (intensity, voltage, board info, target intensity)."""
    # Get device configuration
    devices = await config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    # Check if dimming is enabled
    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    # Get board_id and channel
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} missing dimming configuration"
        )

    payload = _read_light_status_payload(
        location,
        cluster,
        device_name,
        device_info,
        dfr0971_manager,
        database,
        scheduler,
    )
    if not payload:
        raise HTTPException(
            status_code=500, detail=f"Failed to read status for board {board_id}, channel {channel}"
        )
    return payload


@router.get("/api/lights/{location}/{cluster}/{device_name}/schedule")
async def get_light_schedule(
    location: str, cluster: str, device_name: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    schedule = await database.schedule_repo.get_room_light_schedule(location, cluster, device_name)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"No schedule found for {device_name}")
    return schedule


@router.put("/api/lights/{location}/{cluster}/{device_name}/schedule")
async def update_light_schedule(
    location: str,
    cluster: str,
    device_name: str,
    control: ScheduleTimeControl,
    config: ConfigLoader = Depends(get_config),
    database: DatabaseManager = Depends(get_database),
    scheduler: Any = Depends(get_scheduler),
) -> dict[str, Any]:
    updated = await database.update_light_schedule_times(
        location, cluster, device_name, control.start_time, control.end_time
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"No schedule found for {device_name}")

    if scheduler:
        all_schedules = await database.schedule_repo.get_schedules()
        scheduler.update_schedules(await merge_schedules_with_config(all_schedules, config))
        logger.info(f"Scheduler refreshed after {device_name} schedule time update")

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "start_time": control.start_time,
        "end_time": control.end_time,
    }
