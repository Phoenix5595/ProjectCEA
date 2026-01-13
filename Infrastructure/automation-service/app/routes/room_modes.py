"""API routes for room modes management."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from shared.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/room-modes", tags=["room-modes"])


class RoomMode(BaseModel):
    id: int
    name: str
    description: Optional[str]
    photoperiod_hours: Optional[int]
    is_constant: bool


class FlowerSubmode(BaseModel):
    id: int
    name: str
    description: Optional[str]
    week_start: Optional[int]
    week_end: Optional[int]


class ActiveMode(BaseModel):
    location: str
    cluster: str
    mode_name: str
    submode_name: Optional[str]


class SetActiveModeRequest(BaseModel):
    location: str
    cluster: str
    mode_name: str
    submode_name: Optional[str] = None


@router.get("/modes", response_model=List[RoomMode])
async def get_room_modes(db=None):
    """Get all available room modes."""
    # TODO: Inject db from container
    return [
        {"id": 1, "name": "veg", "description": "Vegetative growth", "photoperiod_hours": 18, "is_constant": False},
        {"id": 2, "name": "flower", "description": "Flowering", "photoperiod_hours": 12, "is_constant": False},
        {"id": 3, "name": "drying", "description": "Drying", "photoperiod_hours": 0, "is_constant": True},
        {"id": 4, "name": "sleep", "description": "Sleep mode", "photoperiod_hours": 0, "is_constant": True},
    ]


@router.get("/submodes", response_model=List[FlowerSubmode])
async def get_flower_submodes():
    """Get all flower submodes."""
    return [
        {"id": 1, "name": "stretch", "description": "Stretch phase", "week_start": 1, "week_end": 3},
        {"id": 2, "name": "bulk", "description": "Bulk phase", "week_start": 4, "week_end": 6},
        {"id": 3, "name": "ripen", "description": "Ripen phase", "week_start": 7, "week_end": 9},
    ]


@router.get("/active/{location}/{cluster}", response_model=ActiveMode)
async def get_active_mode(location: str, cluster: str):
    """Get active mode for a room."""
    # TODO: Query from database
    if location == "Flower Room":
        return {"location": location, "cluster": cluster, "mode_name": "flower", "submode_name": "bulk"}
    else:
        return {"location": location, "cluster": cluster, "mode_name": "veg", "submode_name": None}


@router.post("/active")
async def set_active_mode(request: SetActiveModeRequest):
    """Set active mode for a room."""
    logger.info(f"Setting mode {request.mode_name} for {request.location}/{request.cluster}")
    # TODO: Persist to database and trigger mode change
    return {"success": True, "message": f"Mode set to {request.mode_name}"}
