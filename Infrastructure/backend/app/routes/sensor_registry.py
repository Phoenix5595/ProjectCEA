"""Sensor registry and soil API routes.

Registered BEFORE the legacy ``/{location}/{cluster}`` router in
``app/main.py`` so these static paths win route matching.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query

from app.dependencies import get_sensor_registry_repository
from app.repositories.sensor_registry_repository import SensorRegistryRepository
from app.sensor_registry_models import (
    SOIL_HISTORY_MAX_POINTS,
    SOIL_HISTORY_MIN_POINTS,
    CanAssignmentRequest,
    IdentityRequest,
    Rs485AssignmentRequest,
    SensorRegistryListResponse,
    SensorRegistryRecord,
    SoilBedsResponse,
    SoilBusStatus,
    SoilHeatmapResponse,
    SoilHistoryResponse,
    SoilLiveResponse,
)

router = APIRouter(prefix="/api/sensors", tags=["sensor-registry"])


@router.get("/registry", response_model=SensorRegistryListResponse)
async def list_sensor_registry(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
    status: str = Query("all", pattern="^(all|assigned|unassigned)$"),
    bus: str | None = Query(None, pattern="^(can|rs485)$"),
) -> SensorRegistryListResponse:
    """List physical sensor registry records (CAN nodes and RS-485 probes)."""
    records, unassigned_count = await repository.list_records(status=status, bus=bus)
    return SensorRegistryListResponse(records=records, unassigned_count=unassigned_count)


# Static RS-485 routes are declared before any ``{registry_id}`` route.
@router.get("/registry/rs485/bus-status", response_model=SoilBusStatus)
async def soil_bus_status(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
) -> SoilBusStatus:
    """RS-485 commissioning state published by the soil service (``unknown`` when absent)."""
    return await repository.soil_bus_status()


@router.get("/registry/rs485/beds", response_model=SoilBedsResponse)
async def soil_beds(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
) -> SoilBedsResponse:
    """Flower Room soil beds with dimensions, Back Bed then Front Bed."""
    return SoilBedsResponse(beds=await repository.soil_beds())


@router.get("/registry/rs485/heatmap", response_model=SoilHeatmapResponse)
async def soil_heatmap(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
    metric: str = Query("water_content", pattern="^(water_content|temperature|ec|ph)$"),
) -> SoilHeatmapResponse:
    """Current and recently closed (72 h) bed placements with their 10-minute mean value."""
    return await repository.soil_heatmap(metric)


@router.put("/registry/{registry_id}/assignment")
async def assign_sensor_registry(
    registry_id: int,
    body: Annotated[CanAssignmentRequest | Rs485AssignmentRequest, Body(discriminator="kind")],
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
):
    """Assign a CAN node to a room position, or place an RS-485 probe (Flower bed spot or Veg Room)."""
    return await repository.assign(registry_id, body)


@router.delete("/registry/{registry_id}/assignment", response_model=SensorRegistryRecord)
async def unassign_sensor_registry(
    registry_id: int,
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
) -> SensorRegistryRecord:
    """Move an RS-485 probe to Unassigned (409 ``not_rs485`` for CAN nodes)."""
    return await repository.unassign(registry_id)


@router.post("/registry/{registry_id}/identity", response_model=SensorRegistryRecord)
async def resolve_sensor_registry_identity(
    registry_id: int,
    body: Annotated[IdentityRequest, Body(discriminator="decision")],
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
) -> SensorRegistryRecord:
    """Confirm whether a newly observed address belongs to an older identity."""
    return await repository.resolve_identity(registry_id, body)


@router.get("/soil/live", response_model=SoilLiveResponse)
async def get_soil_live(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
) -> SoilLiveResponse:
    """Live metric values for every assigned RS-485 soil probe."""
    return await repository.soil_live()


@router.get("/soil/history", response_model=SoilHistoryResponse)
async def get_soil_history(
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
    start: datetime = Query(...),
    end: datetime = Query(...),
    max_points: Annotated[
        int, Query(..., ge=SOIL_HISTORY_MIN_POINTS, le=SOIL_HISTORY_MAX_POINTS)
    ] = 500,
) -> SoilHistoryResponse:
    """Envelope history for assigned RS-485 probes within an aware UTC window.

    The range itself (5 min .. 7 days, aware UTC, ``end > start``) is
    enforced by the repository; violations surface as structured 422s.
    """
    return await repository.soil_history(start=start, end=end, max_points=max_points)
