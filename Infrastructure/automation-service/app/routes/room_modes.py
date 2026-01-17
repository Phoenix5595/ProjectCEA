from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from shared.logging import get_logger
from app.database import DatabaseManager

logger = get_logger(__name__)
router = APIRouter(prefix="/api/room-modes", tags=["room-modes"])


def get_database() -> DatabaseManager:
    from app.main import container
    return container.get_database()


class RoomMode(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    photoperiod_hours: Optional[int] = None
    is_constant: bool = False


class FlowerSubmode(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    week_start: Optional[int] = None
    week_end: Optional[int] = None


class ActiveModeResponse(BaseModel):
    location: str
    cluster: str
    mode_name: str
    submode_name: Optional[str] = None
    mode_id: Optional[int] = None
    submode_id: Optional[int] = None


class ModeParameters(BaseModel):
    day_start_time: str = "06:00"
    night_start_time: str = "18:00"
    ramp_up_minutes: int = 30
    ramp_down_minutes: int = 30
    pre_day_minutes: int = 30
    pre_night_minutes: int = 30
    day_heat_temp: float = 24.0
    day_cool_temp: float = 28.0
    day_vpd: float = 1.0
    day_co2: int = 800
    day_leaf_delta: float = -2.0
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
    submode_name: Optional[str] = None
    is_constant: bool = False
    parameters: ModeParameters


class SetModeRequest(BaseModel):
    mode_name: str
    submode_name: Optional[str] = None


class UpdateParametersRequest(BaseModel):
    day_start_time: Optional[str] = None
    night_start_time: Optional[str] = None
    ramp_up_minutes: Optional[int] = None
    ramp_down_minutes: Optional[int] = None
    pre_day_minutes: Optional[int] = None
    pre_night_minutes: Optional[int] = None
    day_heat_temp: Optional[float] = None
    day_cool_temp: Optional[float] = None
    day_vpd: Optional[float] = None
    day_co2: Optional[int] = None
    day_leaf_delta: Optional[float] = None
    night_heat_temp: Optional[float] = None
    night_cool_temp: Optional[float] = None
    night_vpd: Optional[float] = None
    night_co2: Optional[int] = None
    night_leaf_delta: Optional[float] = None
    main_light_intensity: Optional[int] = None
    supplemental_light_intensity: Optional[int] = None


@router.get("/modes", response_model=list[RoomMode])
async def get_room_modes(db: DatabaseManager = Depends(get_database)):
    modes = await db.get_room_modes()
    return modes


@router.get("/submodes", response_model=list[FlowerSubmode])
async def get_flower_submodes(db: DatabaseManager = Depends(get_database)):
    submodes = await db.get_flower_submodes()
    return submodes


@router.get("/active/{location}/{cluster}", response_model=ActiveModeResponse)
async def get_active_mode(location: str, cluster: str, db: DatabaseManager = Depends(get_database)):
    active = await db.get_active_mode(location, cluster)
    if not active:
        if "flower" in location.lower():
            return ActiveModeResponse(location=location, cluster=cluster, mode_name="flower", submode_name="bulk")
        return ActiveModeResponse(location=location, cluster=cluster, mode_name="veg", submode_name=None)
    return ActiveModeResponse(**active)


@router.get("/room/{location}/{cluster}", response_model=RoomModeWithParams)
async def get_room_mode_with_params(location: str, cluster: str, db: DatabaseManager = Depends(get_database)):
    active = await db.get_active_mode(location, cluster)
    
    if not active:
        mode_name = "flower" if "flower" in location.lower() else "veg"
        submode_name = "bulk" if mode_name == "flower" else None
    else:
        mode_name = active['mode_name']
        submode_name = active.get('submode_name')
    
    modes = await db.get_room_modes()
    mode_info = next((m for m in modes if m['name'] == mode_name), None)
    is_constant = mode_info['is_constant'] if mode_info else False
    
    params = await db.get_mode_parameters(location, cluster, mode_name, submode_name)
    if not params:
        params = ModeParameters().model_dump()
    else:
        params.pop('id', None)
        params.pop('location', None)
        params.pop('cluster', None)
        params.pop('mode_id', None)
        params.pop('submode_id', None)
        params.pop('created_at', None)
        params.pop('updated_at', None)
    
    return RoomModeWithParams(
        location=location,
        cluster=cluster,
        mode_name=mode_name,
        submode_name=submode_name,
        is_constant=is_constant,
        parameters=ModeParameters(**params)
    )


@router.post("/room/{location}/{cluster}/mode", response_model=RoomModeWithParams)
async def set_room_mode(location: str, cluster: str, request: SetModeRequest, db: DatabaseManager = Depends(get_database)):
    current = await db.get_active_mode(location, cluster)
    if current:
        current_params = await db.get_mode_parameters(location, cluster, current['mode_name'], current.get('submode_name'))
        if current_params:
            await db.save_mode_parameters(location, cluster, current['mode_name'], current.get('submode_name'), current_params)
    
    success = await db.set_active_mode(location, cluster, request.mode_name, request.submode_name)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to set mode '{request.mode_name}'")
    
    logger.info(f"Mode switch: {location}/{cluster} -> {request.mode_name}/{request.submode_name}")
    return await get_room_mode_with_params(location, cluster, db)


@router.put("/room/{location}/{cluster}/parameters", response_model=RoomModeWithParams)
async def update_room_parameters(location: str, cluster: str, request: UpdateParametersRequest, db: DatabaseManager = Depends(get_database)):
    active = await db.get_active_mode(location, cluster)
    if not active:
        mode_name = "flower" if "flower" in location.lower() else "veg"
        submode_name = "bulk" if mode_name == "flower" else None
        await db.set_active_mode(location, cluster, mode_name, submode_name)
    else:
        mode_name = active['mode_name']
        submode_name = active.get('submode_name')
    
    current_params = await db.get_mode_parameters(location, cluster, mode_name, submode_name)
    if not current_params:
        current_params = ModeParameters().model_dump()
    
    updates = request.model_dump(exclude_none=True)
    merged_params = {**current_params, **updates}
    
    await db.save_mode_parameters(location, cluster, mode_name, submode_name, merged_params)
    
    logger.info(f"Parameters updated: {location}/{cluster} mode={mode_name}")
    return await get_room_mode_with_params(location, cluster, db)
