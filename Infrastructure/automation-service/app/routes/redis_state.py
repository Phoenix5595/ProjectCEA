"""Redis state query endpoints for Grafana and other consumers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.database import DatabaseManager
from app.routes.schedules import _build_schedule_state
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/redis-state/schedule/{location}/{cluster}")
async def get_schedule_state(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Get schedule state from database.

    This endpoint is used by Grafana to query current schedule state.

    Args:
        location: Location name (e.g., 'Veg Room', 'Flower Room')
        cluster: Cluster name (e.g., 'main')

    Returns:
        Complete schedule state matching canonical schema structure
    """
    try:
        schedule_state = await _build_schedule_state(database, location, cluster)
        logger.debug(f"Returning schedule state from database for {location}/{cluster}")
        return schedule_state
    except Exception as e:
        logger.error(f"Error getting schedule state for {location}/{cluster}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to retrieve schedule state: {str(e)}"
        ) from e
