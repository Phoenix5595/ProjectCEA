"""Status and health check routes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from app.database import DatabaseManager

router = APIRouter()

_READY_TIMEOUT_SEC = 0.5


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
    """Readiness probe: postgres reachable within 500ms."""
    out: dict[str, Any] = {"service": "weather-service", "checks": {}}
    ok = True

    pool = db._pool
    t0 = time.perf_counter()
    if pool is None:
        out["checks"]["postgres"] = {"ok": False, "detail": "pool not initialized"}
        ok = False
    else:
        try:
            async with pool.acquire() as conn:
                val = await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=_READY_TIMEOUT_SEC)
            out["checks"]["postgres"] = {
                "ok": val == 1,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
            if val != 1:
                ok = False
        except TimeoutError:
            out["checks"]["postgres"] = {
                "ok": False,
                "detail": f"timeout after {_READY_TIMEOUT_SEC * 1000:.0f}ms",
            }
            ok = False
        except Exception as e:
            out["checks"]["postgres"] = {"ok": False, "detail": f"{type(e).__name__}: {e}"}
            ok = False

    out["status"] = "ready" if ok else "not_ready"
    if not ok:
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
