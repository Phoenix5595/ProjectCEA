"""Redis state query endpoints for Grafana and other consumers."""
from shared.logging import get_logger
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Optional

from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from app.routes.schedules import _build_schedule_state

logger = get_logger(__name__)

router = APIRouter()


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_automation_redis() -> Optional[AutomationRedisClient]:
    """Get automation Redis client."""
    from app.main import get_database as _get_database
    database = _get_database()
    return database._automation_redis if database else None


@router.get("/api/redis-state/schedule/{location}/{cluster}")
async def get_schedule_state(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database),
    redis_client: Optional[AutomationRedisClient] = Depends(get_automation_redis)
) -> Dict[str, Any]:
    """Get schedule state from Redis or fallback to database.
    
    This endpoint is used by Grafana to query current schedule state.
    Follows canonical Redis schema: schedule:state:<room>:<cluster>
    
    Args:
        location: Location name (e.g., 'Veg Room', 'Flower Room')
        cluster: Cluster name (e.g., 'main')
    
    Returns:
        Complete schedule state matching canonical schema structure
    """
    # First try Redis state
    if redis_client and redis_client.redis_enabled:
        schedule_state = redis_client.read_schedule_state(location, cluster)
        if schedule_state:
            logger.debug(f"Returning schedule state from Redis for {location}/{cluster}")
            return schedule_state
    
    # Fallback to database
    try:
        schedule_state = await _build_schedule_state(database, location, cluster)
        logger.debug(f"Returning schedule state from database for {location}/{cluster}")
        
        # Optionally write back to Redis for future queries
        if redis_client and redis_client.redis_enabled:
            try:
                redis_client.write_schedule_state(location, cluster, schedule_state)
            except Exception as e:
                logger.warning(f"Failed to write schedule state to Redis after DB fallback: {e}")
        
        return schedule_state
    except Exception as e:
        logger.error(f"Error getting schedule state for {location}/{cluster}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve schedule state: {str(e)}"
        )
