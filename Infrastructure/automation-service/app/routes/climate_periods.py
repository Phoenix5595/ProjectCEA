from __future__ import annotations

from datetime import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import DatabaseManager
from app.dependencies import get_database

router = APIRouter(prefix="/api/climate-periods", tags=["climate-periods"])


class PeriodInput(BaseModel):
    period_name: str
    start_time: str
    end_time: str
    ramp_minutes: int = 0
    heating_setpoint: float | None = None
    cooling_setpoint: float | None = None
    vpd_setpoint: float | None = None
    co2_setpoint: int | None = None
    details: str | None = None


class PeriodsSaveRequest(BaseModel):
    periods: list[PeriodInput]
    mode_id: int | None = None
    submode_id: int | None = None


class MigrationRequest(BaseModel):
    mode_name: str = "Flower"
    submode_name: str | None = None
    dry_run: bool = True


@router.get("/{location}/{cluster}")
async def get_climate_periods(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> list[dict[str, Any]]:
    """Get all climate periods for a location/cluster."""
    periods = await database.climate_periods_repo.get_periods(location, cluster)
    return [dict(p) for p in periods]


@router.post("/{location}/{cluster}")
async def save_climate_periods(
    location: str,
    cluster: str,
    request: PeriodsSaveRequest,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Save climate periods for a location/cluster."""
    valid, errors = database.climate_periods_repo.validate_24h_coverage(
        [p.model_dump() for p in request.periods]
    )

    if not valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    await database.climate_periods_repo.delete_periods(
        location, cluster, request.mode_id, request.submode_id
    )

    saved = []
    for p in request.periods:
        result = await database.climate_periods_repo.save_period(
            location=location,
            cluster=cluster,
            period_name=p.period_name,
            start_time=p.start_time,
            end_time=p.end_time,
            ramp_minutes=p.ramp_minutes,
            heating_setpoint=p.heating_setpoint,
            cooling_setpoint=p.cooling_setpoint,
            vpd_setpoint=p.vpd_setpoint,
            co2_setpoint=p.co2_setpoint,
            details=p.details,
            mode_id=request.mode_id,
            submode_id=request.submode_id,
        )
        if result:
            saved.append(result)

    return {"saved": len(saved), "periods": saved}


@router.get("/{location}/{cluster}/validate")
async def validate_climate_periods(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Validate 24h coverage for climate periods."""
    periods = await database.climate_periods_repo.get_periods(location, cluster)
    valid, errors = database.climate_periods_repo.validate_24h_coverage([dict(p) for p in periods])
    return {"valid": valid, "errors": errors, "period_count": len(periods)}


@router.get("/{location}/{cluster}/active")
async def get_active_period(
    location: str,
    cluster: str,
    time: str = "00:00",
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any] | None:
    """Get the active climate period at a given time."""
    period = await database.climate_periods_repo.get_active_period(location, cluster, time)
    return dict(period) if period else None


@router.delete("/{location}/{cluster}")
async def delete_climate_periods(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Delete all climate periods for a location/cluster."""
    success = await database.climate_periods_repo.delete_periods(location, cluster)
    return {"deleted": success}
