"""Status and health check routes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from app.database import DatabaseManager

router = APIRouter()

# Per-dependency budget for readiness checks. Deliberately tight: deploy
# health-checkers retry, and a slow /ready is worse than a 503 that fails
# fast.
_READY_TIMEOUT_SEC = 0.5


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
    the asyncpg pool) with a per-check timeout. Returns 503 on any failure
    so orchestrators drain traffic. Never raises — always returns JSON.
    """
    out: dict[str, Any] = {
        "service": "soil-sensor-service",
        "checks": {},
    }
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
            out["checks"]["postgres"] = {
                "ok": False,
                "detail": f"{type(e).__name__}: {e}",
            }
            ok = False

    out["status"] = "ready" if ok else "not_ready"
    if not ok:
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
