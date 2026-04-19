"""Status and health check routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from app.database import DatabaseManager
from shared.health import all_ok, check_postgres_pool

router = APIRouter()


def get_database() -> DatabaseManager:
    """Get database manager."""
    raise NotImplementedError("Dependency not injected")


@router.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"service": "Weather Service", "version": "1.0.0", "status": "running"}


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Cheap — process responds. Does not touch DB/Redis."""
    return {"status": "healthy"}


@router.get("/ready")
async def ready(
    response: Response,
    db: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Readiness probe: postgres reachable within 500ms (see shared.health)."""
    out: dict[str, Any] = {
        "service": "weather-service",
        "checks": {"postgres": await check_postgres_pool(db._pool)},
    }
    out["status"] = "ready" if all_ok(out["checks"]) else "not_ready"
    if out["status"] != "ready":
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE
    return out


@router.get("/status")
async def status(db: DatabaseManager = Depends(get_database)) -> dict[str, Any]:
    """Detailed status endpoint."""
    return {
        "service": "Weather Service",
        "version": "1.0.0",
        "database_connected": db._db_connected,
        "status": "running",
    }
