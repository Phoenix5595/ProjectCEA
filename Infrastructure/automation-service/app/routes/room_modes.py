from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.infra_logging import get_logger

from ..database import DatabaseManager
from ..events import ConfigChangeEvent, ConfigEventType, get_event_bus
from ..services.mode_transition_service import ModeTransitionService
from .websocket import broadcast_mode_update

logger = get_logger(__name__)
router = APIRouter(prefix="/api/room-modes", tags=["room-modes"])


def get_database() -> DatabaseManager:
    from ..main import container

    return container.get_database()


class RoomMode(BaseModel):
    id: int
    name: str
    description: str | None = None
    photoperiod_hours: int | None = None
    is_constant: bool = False


class FlowerSubmode(BaseModel):
    id: int
    name: str
    description: str | None = None
    week_start: int | None = None
    week_end: int | None = None


class ActiveModeResponse(BaseModel):
    location: str
    cluster: str
    mode_name: str
    submode_name: str | None = None
    mode_id: int | None = None
    submode_id: int | None = None


class ModeParameters(BaseModel):
    day_start_time: str = "17:00"
    night_start_time: str = "11:00"
    ramp_up_minutes: int = 30
    ramp_down_minutes: int = 30
    pre_day_ramp_minutes: int = 30
    pre_night_ramp_minutes: int = 30
    pre_day_minutes: int = 30
    pre_night_minutes: int = 30
    light_ramp_up_minutes: int = 15
    light_ramp_down_minutes: int = 15
    pre_day_heat_temp: float = 22.0
    pre_day_cool_temp: float = 26.0
    pre_day_vpd: float = 0.9
    pre_day_co2: int = 700
    day_heat_temp: float = 24.0
    day_cool_temp: float = 28.0
    day_vpd: float = 1.0
    day_co2: int = 800
    day_leaf_delta: float = -2.0
    pre_night_heat_temp: float = 22.0
    pre_night_cool_temp: float = 26.0
    pre_night_vpd: float = 0.9
    pre_night_co2: int = 700
    night_heat_temp: float = 20.0
    night_cool_temp: float = 24.0
    night_vpd: float = 0.8
    night_co2: int = 600
    night_leaf_delta: float = -1.0
    main_light_intensity: int = 100
    supplemental_light_intensity: int = 0


class RoomModeWithParams(BaseModel):
    location: str
    cluster: str
    mode_name: str
    submode_name: str | None = None
    is_constant: bool = False
    parameters: ModeParameters


class SetModeRequest(BaseModel):
    mode_name: str
    submode_name: str | None = None
    coordinate_clusters: bool = True  # When True, switch all clusters in location together


class UpdateParametersRequest(BaseModel):
    day_start_time: str | None = None
    night_start_time: str | None = None
    ramp_up_minutes: int | None = None
    ramp_down_minutes: int | None = None
    pre_day_ramp_minutes: int | None = None
    pre_night_ramp_minutes: int | None = None
    pre_day_minutes: int | None = None
    pre_night_minutes: int | None = None
    light_ramp_up_minutes: int | None = None
    light_ramp_down_minutes: int | None = None
    pre_day_heat_temp: float | None = None
    pre_day_cool_temp: float | None = None
    pre_day_vpd: float | None = None
    pre_day_co2: int | None = None
    day_heat_temp: float | None = None
    day_cool_temp: float | None = None
    day_vpd: float | None = None
    day_co2: int | None = None
    day_leaf_delta: float | None = None
    pre_night_heat_temp: float | None = None
    pre_night_cool_temp: float | None = None
    pre_night_vpd: float | None = None
    pre_night_co2: int | None = None
    night_heat_temp: float | None = None
    night_cool_temp: float | None = None
    night_vpd: float | None = None
    night_co2: int | None = None
    night_leaf_delta: float | None = None
    main_light_intensity: int | None = None
    supplemental_light_intensity: int | None = None


@router.get("/modes", response_model=list[RoomMode])
async def get_room_modes(db: DatabaseManager = Depends(get_database)):
    modes = await db.room_mode_repo.get_room_modes()
    return modes


@router.get("/submodes", response_model=list[FlowerSubmode])
async def get_flower_submodes(db: DatabaseManager = Depends(get_database)):
    submodes = await db.room_mode_repo.get_flower_submodes()
    return submodes


@router.get("/active/{location}/{cluster}", response_model=ActiveModeResponse)
async def get_active_mode(location: str, cluster: str, db: DatabaseManager = Depends(get_database)):
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
    location: str, cluster: str, db: DatabaseManager = Depends(get_database)
):
    active = await db.room_mode_repo.get_active_mode(location, cluster)

    if not active:
        mode_name = "flower" if "flower" in location.lower() else "veg"
        submode_name = "bulk" if mode_name == "flower" else None
    else:
        mode_name = active["mode_name"]
        submode_name = active.get("submode_name")

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
        is_constant=is_constant,
        parameters=ModeParameters(**params),
    )


@router.post("/room/{location}/{cluster}/mode", response_model=RoomModeWithParams)
async def set_room_mode(
    location: str,
    cluster: str,
    request: SetModeRequest,
    db: DatabaseManager = Depends(get_database),
):
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

    start = time.perf_counter()
    result = await get_room_mode_with_params(location, cluster, db)
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
):
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
    return await get_room_mode_with_params(location, cluster, db)
