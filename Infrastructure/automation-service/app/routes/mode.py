"""Mode management endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, Optional
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient

router = APIRouter()


class ModeUpdate(BaseModel):
    mode: str  # 'auto', 'manual', 'override', 'failsafe'
    source: str = "api"  # 'api', 'system'


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import get_database as _get_database
    return _get_database()


def get_automation_redis() -> Optional[AutomationRedisClient]:
    """Get automation Redis client."""
    database = get_database()
    return database._automation_redis if database else None


@router.get("/api/mode/{location}/{cluster}")
async def get_mode(
    location: str,
    cluster: str,
    automation_redis: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Any]:
    """Get mode for a location/cluster.
    
    Returns:
        Dict with mode and source information
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    mode = automation_redis.read_mode(location, cluster)
    if mode is None:
        # Default to 'auto' if not set
        mode = 'auto'
        automation_redis.write_mode(location, cluster, mode, source='system')
    
    return {
        "location": location,
        "cluster": cluster,
        "mode": mode
    }


@router.post("/api/mode/{location}/{cluster}")
async def set_mode(
    location: str,
    cluster: str,
    update: ModeUpdate,
    automation_redis: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Any]:
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
    valid_modes = ['auto', 'manual', 'override', 'failsafe']
    if update.mode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Mode must be one of: {', '.join(valid_modes)}"
        )
    
    # Note: Setting mode to 'failsafe' should be done via alarm system, not directly
    # But we allow it for system use
    if update.mode == 'failsafe' and update.source != 'system':
        raise HTTPException(
            status_code=403,
            detail="Cannot set mode to 'failsafe' directly. Use alarm system."
        )
    
    # Write mode to Redis
    success = automation_redis.write_mode(location, cluster, update.mode, source=update.source)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to set mode")
    
    return {
        "location": location,
        "cluster": cluster,
        "mode": update.mode,
        "source": update.source,
        "success": True
    }


@router.get("/api/mode")
async def get_all_modes(
    automation_redis: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Dict[str, str]]:
    """Get all modes for all locations/clusters.
    
    Returns:
        Dict mapping "location:cluster" to mode dict
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    # Scan for all mode keys
    modes = {}
    try:
        for key in automation_redis.redis_client.scan_iter(match="mode:*"):
            # Parse key: mode:location:cluster
            parts = key.split(':')
            if len(parts) >= 3:
                location = parts[1]
                cluster = parts[2]
                mode = automation_redis.redis_client.get(key)
                if mode:
                    modes[f"{location}:{cluster}"] = {
                        "location": location,
                        "cluster": cluster,
                        "mode": mode
                    }
    except Exception as e:
        # If scan fails, return empty dict
        pass
    
    return modes

