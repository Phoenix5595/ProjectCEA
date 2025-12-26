"""Failsafe management endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Optional
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from app.alarm_manager import AlarmManager

router = APIRouter()


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import get_database as _get_database
    return _get_database()


def get_automation_redis() -> Optional[AutomationRedisClient]:
    """Get automation Redis client."""
    database = get_database()
    return database._automation_redis if database else None


def get_alarm_manager() -> Optional[AlarmManager]:
    """Get alarm manager."""
    from app.main import alarm_manager
    return alarm_manager


@router.get("/api/failsafe/{location}/{cluster}")
async def get_failsafe(
    location: str,
    cluster: str,
    automation_redis: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Any]:
    """Get failsafe details for a location/cluster.
    
    Returns:
        Dict with failsafe information (reason, triggered_by, since) or None if not in failsafe
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    failsafe = automation_redis.read_failsafe(location, cluster)
    
    if failsafe is None:
        return {
            "location": location,
            "cluster": cluster,
            "active": False
        }
    
    return {
        "location": location,
        "cluster": cluster,
        "active": True,
        **failsafe
    }


@router.get("/api/failsafe")
async def get_all_failsafes(
    automation_redis: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Dict[str, Any]]:
    """Get all failsafe states.
    
    Returns:
        Dict mapping "location:cluster" to failsafe information
    """
    if not automation_redis or not automation_redis.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    failsafes = {}
    try:
        for key in automation_redis.redis_client.scan_iter(match="failsafe:*"):
            # Parse key: failsafe:location:cluster
            parts = key.split(':')
            if len(parts) >= 3:
                location = parts[1]
                cluster = parts[2]
                failsafe_data = automation_redis.read_failsafe(location, cluster)
                if failsafe_data:
                    failsafes[f"{location}:{cluster}"] = {
                        "location": location,
                        "cluster": cluster,
                        **failsafe_data
                    }
    except Exception as e:
        # If scan fails, return empty dict
        pass
    
    return failsafes


@router.post("/api/failsafe/{location}/{cluster}/clear")
async def clear_failsafe(
    location: str,
    cluster: str,
    alarm_manager: Optional[AlarmManager] = Depends(get_alarm_manager)
) -> Dict[str, Any]:
    """Clear failsafe mode for a location/cluster.
    
    This will only succeed if:
    - No critical alarms are active
    - All conditions for safe operation are met
    
    Returns:
        Success status
    """
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")
    
    success = alarm_manager.clear_failsafe(location, cluster)
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Cannot clear failsafe: critical alarms still active or conditions not met"
        )
    
    return {
        "location": location,
        "cluster": cluster,
        "success": True,
        "message": "Failsafe cleared, mode set to 'auto'"
    }

