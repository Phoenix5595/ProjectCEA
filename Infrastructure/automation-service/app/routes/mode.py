"""Mode management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from app.state import get_state_manager
from shared.infra_logging import get_logger

router = APIRouter()

logger = get_logger(__name__)


class ModeUpdate(BaseModel):
    mode: str  # 'auto', 'manual', 'override', 'failsafe'
    source: str = "api"  # 'api', 'system'


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import container

    return container.get_database()


def get_automation_redis() -> AutomationRedisClient | None:
    """Get automation Redis client."""
    from app.main import container

    return container.get_automation_redis()


@router.get("/api/mode/{location}/{cluster}")
async def get_mode(
    location: str,
    cluster: str,
    automation_redis: AutomationRedisClient | None = Depends(get_automation_redis),
) -> dict[str, Any]:
    """Get mode for a location/cluster.

    Priority: StateManager cache -> Redis fallback.
    """
    # StateManager first (cache with Redis fallback)
    state = get_state_manager()
    mode = await state.get_mode(location, cluster)
    if mode is not None:
        return {"location": location, "cluster": cluster, "mode": mode}

    # Fallback to Redis (cross-service visibility) if not present in StateManager
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")

    mode = automation_redis.read_mode(location, cluster)
    if mode is None:
        # Default to 'auto' if not set
        mode = "auto"
        automation_redis.write_mode(location, cluster, mode, source="system")

    # Populate StateManager cache for future quick reads
    try:
        await state.set_mode(location, cluster, mode, source="redis")
    except Exception as e:
        logger.error(f"Failed to cache mode for {location}/{cluster}: {e}", exc_info=True)
        # Do not fail the request if caching fails

    return {"location": location, "cluster": cluster, "mode": mode}


@router.post("/api/mode/{location}/{cluster}")
async def set_mode(
    location: str,
    cluster: str,
    update: ModeUpdate,
    automation_redis: AutomationRedisClient | None = Depends(get_automation_redis),
) -> dict[str, Any]:
    """Set mode for a location/cluster.

    Args:
        location: Location name
        cluster: Cluster name
        update: Mode update request

    Returns:
        Updated mode information
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")

    # Validate mode
    valid_modes = ["auto", "manual", "override", "failsafe"]
    if update.mode not in valid_modes:
        raise HTTPException(
            status_code=400, detail=f"Mode must be one of: {', '.join(valid_modes)}"
        )

    # Note: Setting mode to 'failsafe' should be done via alarm system, not directly
    # But we allow it for system use
    if update.mode == "failsafe" and update.source != "system":
        raise HTTPException(
            status_code=403, detail="Cannot set mode to 'failsafe' directly. Use alarm system."
        )

    # Write mode to Redis
    success = automation_redis.write_mode(location, cluster, update.mode, source=update.source)

    # Also write to StateManager for in-process cache visibility
    state = get_state_manager()
    try:
        await state.set_mode(location, cluster, update.mode, source=update.source)
    except Exception as e:
        logger.error(f"Failed to cache mode for {location}/{cluster}: {e}", exc_info=True)
        # Non-fatal if state cache update fails

    if not success:
        raise HTTPException(status_code=500, detail="Failed to set mode")

    return {
        "location": location,
        "cluster": cluster,
        "mode": update.mode,
        "source": update.source,
        "success": True,
    }


@router.get("/api/mode")
async def get_all_modes(
    automation_redis: AutomationRedisClient | None = Depends(get_automation_redis),
) -> dict[str, dict[str, str]]:
    """Get all modes for all locations/clusters.

    Returns:
        Dict mapping "location:cluster" to mode dict
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")

    # Scan for all mode keys
    modes = {}
    try:
        assert automation_redis.redis_client is not None, "Redis client must be connected"
        for key in automation_redis.redis_client.scan_iter(match="mode:*"):
            # Parse key: mode:location:cluster
            parts = key.split(":")
            if len(parts) >= 3:
                location = parts[1]
                cluster = parts[2]
                mode = automation_redis.redis_client.get(key)
                if mode:
                    modes[f"{location}:{cluster}"] = {
                        "location": location,
                        "cluster": cluster,
                        "mode": mode,
                    }
    except (ConnectionError, OSError) as e:
        logger.error(f"Failed to scan mode keys: {e}", exc_info=True)
        # If scan fails, return empty dict

    return modes
