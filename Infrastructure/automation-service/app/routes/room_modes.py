from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException

from app.cluster_config import ensure_configured_cluster, iter_flower_main_merged_devices
from app.config import ConfigLoader
from app.control.relay_manager import RelayManager
from app.schemas.room_modes import (
    ActiveModeResponse,
    FlowerSubmode,
    ModeParameters,
    RoomMode,
    RoomModeWithParams,
    SetModeRequest,
    UpdateParametersRequest,
)
from shared.infra_logging import get_logger
from shared.room_light_authority import is_moon_authority_mode

from ..database import DatabaseManager
from ..events import ConfigChangeEvent, ConfigEventType, get_event_bus
from ..services.mode_transition_service import ModeTransitionService
from .websocket import broadcast_mode_update

logger = get_logger(__name__)
router = APIRouter(prefix="/api/room-modes", tags=["room-modes"])


def get_database() -> DatabaseManager:
    from ..main import container

    return container.get_database()


def get_config() -> ConfigLoader:
    from ..main import container

    return container.get_config()


def get_relay_manager() -> RelayManager:
    from ..main import container

    return container.get_relay_manager()


def get_dfr0971_manager():
    from ..main import container

    return container.get_dfr0971_manager()


def _iter_configured_light_devices(
    config: ConfigLoader, location: str, cluster: str
) -> list[tuple[str, str, dict]]:
    devices = config.get_devices()
    location_config = devices.get(location, {}) or {}
    if location == "Flower Room" and cluster == "main":
        device_entries = iter_flower_main_merged_devices(location_config)
    else:
        raw = location_config.get(cluster, {}) or {}
        device_entries = [
            (cluster, name, info) for name, info in raw.items() if isinstance(info, dict)
        ]

    lights: list[tuple[str, str, dict]] = []
    seen: set[str] = set()
    for src_cluster, device_name, device_info in device_entries:
        if device_name in seen:
            continue
        seen.add(device_name)
        if device_info.get("device_type") == "light":
            lights.append((src_cluster, device_name, device_info))
    return lights


async def _force_lights_off_for_moon_authority_mode(
    location: str,
    cluster: str,
    config: ConfigLoader,
    relay_manager: RelayManager,
    dfr0971_manager,
    database: DatabaseManager,
) -> None:
    for src_cluster, device_name, device_info in _iter_configured_light_devices(
        config, location, cluster
    ):
        board_id = device_info.get("dimming_board_id")
        dimming_channel = device_info.get("dimming_channel")
        if (
            dfr0971_manager is not None
            and device_info.get("dimming_enabled")
            and board_id is not None
            and dimming_channel is not None
        ):
            dimmer_ok = await asyncio.to_thread(
                dfr0971_manager.set_intensity, board_id, dimming_channel, 0
            )
            if not dimmer_ok:
                logger.warning(
                    "Moon-authority mode failed to force DFR0971 intensity to 0 for %s/%s/%s",
                    location,
                    src_cluster,
                    device_name,
                )
            automation_redis = getattr(database, "_automation_redis", None)
            if automation_redis:
                automation_redis.write_light_intensity(
                    location, src_cluster, device_name, 0.0, 0.0, board_id, dimming_channel
                )

        success, reason = await asyncio.to_thread(
            relay_manager.set_device_state, location, src_cluster, device_name, 0
        )
        if not success:
            logger.warning(
                "Moon-authority mode failed to force light relay OFF for %s/%s/%s: %s",
                location,
                src_cluster,
                device_name,
                reason,
            )


@router.get("/modes", response_model=list[RoomMode])
async def get_room_modes(db: DatabaseManager = Depends(get_database)):
    modes = await db.room_mode_repo.get_room_modes()
    return modes


@router.get("/submodes", response_model=list[FlowerSubmode])
async def get_flower_submodes(db: DatabaseManager = Depends(get_database)):
    submodes = await db.room_mode_repo.get_flower_submodes()
    return submodes


@router.get("/active/{location}/{cluster}", response_model=ActiveModeResponse)
async def get_active_mode(
    location: str,
    cluster: str,
    db: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
):
    ensure_configured_cluster(config.get_devices(), location, cluster)
    active = await db.room_mode_repo.get_active_mode(location, cluster)
    if not active:
        if "flower" in location.lower():
            return ActiveModeResponse(
                location=location, cluster=cluster, mode_name="flower", submode_name="bulk"
            )
        return ActiveModeResponse(
            location=location, cluster=cluster, mode_name="veg", submode_name=None
        )
    return ActiveModeResponse(**active)


@router.get("/room/{location}/{cluster}", response_model=RoomModeWithParams)
async def get_room_mode_with_params(
    location: str,
    cluster: str,
    db: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
):
    ensure_configured_cluster(config.get_devices(), location, cluster)
    active = await db.room_mode_repo.get_active_mode(location, cluster)

    if not active:
        mode_name = "flower" if "flower" in location.lower() else "veg"
        submode_name = "bulk" if mode_name == "flower" else None
        mode_id = None
        submode_id = None
    else:
        mode_name = active["mode_name"]
        submode_name = active.get("submode_name")
        mode_id = active.get("mode_id")
        submode_id = active.get("submode_id")

    # If mode_id is still None, look it up from mode_name
    if mode_id is None:
        modes = await db.room_mode_repo.get_room_modes()
        mode_info = next((m for m in modes if m["name"] == mode_name), None)
        mode_id = mode_info["id"] if mode_info else None

    # If submode_id is still None but we have a submode_name, look it up
    if submode_id is None and submode_name:
        submodes = await db.room_mode_repo.get_flower_submodes()
        submode_info = next((s for s in submodes if s["name"] == submode_name), None)
        submode_id = submode_info["id"] if submode_info else None

    modes = await db.room_mode_repo.get_room_modes()
    mode_info = next((m for m in modes if m["name"] == mode_name), None)
    is_constant = mode_info["is_constant"] if mode_info else False

    params = await db.room_mode_repo.get_mode_parameters(location, cluster, mode_name, submode_name)
    if not params:
        params = ModeParameters().model_dump()
    else:
        params.pop("id", None)
        params.pop("location", None)
        params.pop("cluster", None)
        params.pop("mode_id", None)
        params.pop("submode_id", None)
        params.pop("created_at", None)
        params.pop("updated_at", None)

    return RoomModeWithParams(
        location=location,
        cluster=cluster,
        mode_name=mode_name,
        submode_name=submode_name,
        mode_id=mode_id,
        submode_id=submode_id,
        is_constant=is_constant,
        parameters=ModeParameters(**params),
    )


@router.post("/room/{location}/{cluster}/mode", response_model=RoomModeWithParams)
async def set_room_mode(
    location: str,
    cluster: str,
    request: SetModeRequest,
    db: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
    relay_manager: RelayManager = Depends(get_relay_manager),
    dfr0971_manager=Depends(get_dfr0971_manager),
):
    ensure_configured_cluster(config.get_devices(), location, cluster)
    total_start = time.perf_counter()

    # Resolve IDs for the new transition service
    modes = await db.room_mode_repo.get_room_modes()
    mode_info = next((m for m in modes if m["name"] == request.mode_name), None)
    if not mode_info:
        raise HTTPException(status_code=400, detail=f"Mode '{request.mode_name}' not found")
    mode_id = mode_info["id"]

    submode_id = None
    if request.submode_name:
        submodes = await db.room_mode_repo.get_flower_submodes()
        submode_info = next((s for s in submodes if s["name"] == request.submode_name), None)
        if not submode_info:
            raise HTTPException(
                status_code=400, detail=f"Submode '{request.submode_name}' not found"
            )
        submode_id = submode_info["id"]

    transition_service = ModeTransitionService(db)

    start = time.perf_counter()
    result_data = await transition_service.execute_mode_transition(
        location=location,
        cluster=cluster,
        new_mode_id=mode_id,
        new_submode_id=submode_id,
        triggered_by="api",
    )
    transaction_time = (time.perf_counter() - start) * 1000
    logger.info(f"MODE_SWITCH_TIMING: transition service took {transaction_time:.2f}ms")

    if not result_data.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result_data.get("message") or f"Failed to set mode '{request.mode_name}'",
        )

    logger.info(f"Mode switch: {location}/{cluster} -> {request.mode_name}/{request.submode_name}")

    if is_moon_authority_mode(request.mode_name.strip()):
        await _force_lights_off_for_moon_authority_mode(
            location, cluster, config, relay_manager, dfr0971_manager, db
        )

    start = time.perf_counter()
    result = await get_room_mode_with_params(location, cluster, db=db, config=config)
    logger.info(
        f"MODE_SWITCH_TIMING: get_room_mode_with_params took {(time.perf_counter() - start) * 1000:.2f}ms"
    )

    total_time = (time.perf_counter() - total_start) * 1000
    logger.info(f"MODE_SWITCH_TIMING: total endpoint time {total_time:.2f}ms")

    # Broadcast mode update to frontend
    try:
        await broadcast_mode_update(location, cluster, request.mode_name)
    except Exception as e:
        logger.error(f"Failed to broadcast mode update: {e}")

    return result


@router.put("/room/{location}/{cluster}/parameters", response_model=RoomModeWithParams)
async def update_room_parameters(
    location: str,
    cluster: str,
    request: UpdateParametersRequest,
    db: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
):
    ensure_configured_cluster(config.get_devices(), location, cluster)
    active = await db.room_mode_repo.get_active_mode(location, cluster)
    if not active:
        mode_name = "flower" if "flower" in location.lower() else "veg"
        submode_name = "bulk" if mode_name == "flower" else None
        await db.room_mode_repo.set_active_mode(location, cluster, mode_name, submode_name)
    else:
        mode_name = active["mode_name"]
        submode_name = active.get("submode_name")

    current_params = await db.room_mode_repo.get_mode_parameters(
        location, cluster, mode_name, submode_name
    )
    if not current_params:
        current_params = ModeParameters().model_dump()

    updates = request.model_dump(exclude_none=True)
    merged_params = {**current_params, **updates}

    await db.room_mode_repo.save_mode_parameters(
        location, cluster, mode_name, submode_name, merged_params
    )

    # Sync light ramp times to existing light schedules if they were updated
    if "light_ramp_up_minutes" in updates or "light_ramp_down_minutes" in updates:
        light_ramp_up = merged_params.get("light_ramp_up_minutes", 15)
        light_ramp_down = merged_params.get("light_ramp_down_minutes", 15)

        await db.schedule_repo.update_light_schedule_ramp_times(
            location, cluster, light_ramp_up, light_ramp_down
        )
        logger.info(
            f"Synced light ramp times to schedules: {location}/{cluster} "
            f"ramp_up={light_ramp_up}min, ramp_down={light_ramp_down}min"
        )

        # Publish config change event for immediate scheduler update
        event_bus = get_event_bus()
        event = ConfigChangeEvent(
            event_type=ConfigEventType.RAMP_TIMES_CHANGED,
            location=location,
            cluster=cluster,
            config_type="ramp_times",
            data={"ramp_up_minutes": light_ramp_up, "ramp_down_minutes": light_ramp_down},
        )
        published = await event_bus.publish(event)
        if published:
            logger.info(f"Published RAMP_TIMES_CHANGED event for {location}/{cluster}")
        else:
            logger.warning(
                f"Failed to publish RAMP_TIMES_CHANGED event (queue full) for {location}/{cluster}"
            )

    logger.info(f"Parameters updated: {location}/{cluster} mode={mode_name}")
    return await get_room_mode_with_params(location, cluster, db=db, config=config)
