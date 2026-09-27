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
    Rs485AssignmentRequest,
    SensorRegistryListResponse,
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


@router.put("/registry/{registry_id}/assignment")
async def assign_sensor_registry(
    registry_id: int,
    body: Annotated[CanAssignmentRequest | Rs485AssignmentRequest, Body(discriminator="kind")],
    repository: Annotated[SensorRegistryRepository, Depends(get_sensor_registry_repository)],
):
    """Assign a physical sensor unit to a room position (CAN) or Flower bed (RS-485)."""
    return await repository.assign(registry_id, body)


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
