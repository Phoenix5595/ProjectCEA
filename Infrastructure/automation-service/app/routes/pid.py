"""PID parameter management endpoints."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.database import DatabaseManager
from app.config import ConfigLoader
from app.validation import validate_pid_parameters

logger = logging.getLogger(__name__)

router = APIRouter()


class PIDParameterUpdate(BaseModel):
    """Request model for PID parameter update."""
    kp: Optional[float] = None
    ki: Optional[float] = None
    kd: Optional[float] = None
    source: str = "api"  # 'api', 'config'
    updated_by: Optional[str] = None


# Rate limiting storage (in-memory, per device_type)
_rate_limit_cache: Dict[str, datetime] = {}
_rate_limit_window = 5  # seconds


def check_rate_limit(device_type: str) -> bool:
    """Check if PID parameter update is allowed (rate limiting).
    
    Args:
        device_type: Device type to check
    
    Returns:
        True if update is allowed, False if rate limited
    """
    now = datetime.now()
    last_update = _rate_limit_cache.get(device_type)
    
    if last_update is None:
        _rate_limit_cache[device_type] = now
        return True
    
    time_since_last = (now - last_update).total_seconds()
    if time_since_last >= _rate_limit_window:
        _rate_limit_cache[device_type] = now
        return True
    
    return False


def get_database() -> DatabaseManager:
    """Get database manager."""
    from app.main import get_database as _get_database
    return _get_database()


def get_config() -> ConfigLoader:
    """Get config loader."""
    from app.main import get_config as _get_config
    return _get_config()


@router.get("/api/pid/parameters")
async def get_all_pid_parameters(
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Dict[str, Any]]:
    """Get all PID parameters for all device types.
    
    Returns:
        Dict mapping device_type to parameter dict with kp, ki, kd, updated_at, updated_by, source
    """
    return await database.get_all_pid_parameters()


@router.get("/api/pid/parameters/{device_type}")
async def get_pid_parameters(
    device_type: str,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get PID parameters for a specific device type.
    
    Args:
        device_type: Device type (e.g., 'heater', 'co2')
    
    Returns:
        Dict with kp, ki, kd, updated_at, updated_by, source
    """
    params = await database.get_pid_parameters(device_type)
    if params is None:
        raise HTTPException(status_code=404, detail=f"PID parameters not found for device_type: {device_type}")
    return params


@router.post("/api/pid/parameters/{device_type}")
async def update_pid_parameters(
    device_type: str,
    update: PIDParameterUpdate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Update PID parameters for a device type.
    
    Args:
        device_type: Device type (e.g., 'heater', 'co2')
        update: PID parameter update request
    
    Returns:
        Updated PID parameters
    """
    # Rate limiting check
    if not check_rate_limit(device_type):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum 1 update per {_rate_limit_window} seconds per device_type."
        )
    
    # Get existing parameters to merge with update
    existing = await database.get_pid_parameters(device_type)
    
    # Determine which parameters to update
    kp = update.kp if update.kp is not None else (existing['kp'] if existing else None)
    ki = update.ki if update.ki is not None else (existing['ki'] if existing else None)
    kd = update.kd if update.kd is not None else (existing['kd'] if existing else None)
    
    # Validate parameters
    is_valid, error_message, validated = validate_pid_parameters(
        kp, ki, kd, device_type, config
    )
    
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)
    
    # If no parameters provided, return existing
    if not validated:
        if existing:
            return existing
        raise HTTPException(status_code=400, detail="No parameters provided and no existing parameters found")
    
    # Merge validated parameters with existing (for partial updates)
    final_kp = validated.get('kp', existing['kp'] if existing else None)
    final_ki = validated.get('ki', existing['ki'] if existing else None)
    final_kd = validated.get('kd', existing['kd'] if existing else None)
    
    if final_kp is None or final_ki is None or final_kd is None:
        raise HTTPException(status_code=400, detail="All parameters (kp, ki, kd) must be provided for new device types")
    
    # Update in database
    success = await database.set_pid_parameters(
        device_type,
        final_kp,
        final_ki,
        final_kd,
        source=update.source,
        updated_by=update.updated_by
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update PID parameters")
    
    # Return updated parameters
    updated = await database.get_pid_parameters(device_type)
    return updated


@router.get("/api/pid/parameters/{device_type}/history")
async def get_pid_parameter_history(
    device_type: str,
    limit: int = 100,
    database: DatabaseManager = Depends(get_database)
) -> List[Dict[str, Any]]:
    """Get PID parameter change history for a device type.
    
    Args:
        device_type: Device type (e.g., 'heater', 'co2')
        limit: Maximum number of history entries to return (default: 100)
    
    Returns:
        List of history entries with timestamp, kp, ki, kd, updated_by, source
    """
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")
    
    history = await database.get_pid_parameter_history(device_type, limit)
    return history

