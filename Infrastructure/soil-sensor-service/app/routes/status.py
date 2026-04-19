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
    return {"service": "Soil Sensor Service", "version": "1.0.0", "status": "running"}


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe.

    Cheap — answers "the process is responding". Does not touch DB or Redis.
    Used by deploy.sh post-deploy check today.
    """
    return {"status": "healthy"}


@router.get("/ready")
async def ready(
    response: Response,
    db: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Readiness probe.

    Stricter than /health: verifies every hard dependency (currently just
    the asyncpg pool) via shared.health with a per-check timeout. Returns
    503 on any failure so orchestrators drain traffic. Never raises —
    always returns JSON.
    """
    out: dict[str, Any] = {
        "service": "soil-sensor-service",
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
        "service": "Soil Sensor Service",
        "version": "1.0.0",
        "database_connected": db._db_connected,
        "status": "running",
    }
