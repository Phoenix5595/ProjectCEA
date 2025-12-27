"""Setpoint management endpoints."""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Dict, Optional, Any, List
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.validation import validate_setpoint
from app.redis_client import AutomationRedisClient
from asyncpg.exceptions import PostgresConnectionError, PostgresError
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class SetpointUpdate(BaseModel):
    heating_setpoint: Optional[float] = None
    cooling_setpoint: Optional[float] = None
    humidity: Optional[float] = None
    co2: Optional[float] = None
    vpd: Optional[float] = None
    mode: Optional[str] = None
    ramp_in_duration: Optional[int] = None  # Minutes to ramp in when entering this mode (0 = instant)


# This will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    from app.main import get_config as _get_config
    return _get_config()


def get_automation_redis() -> Optional[AutomationRedisClient]:
    """Get automation Redis client."""
    from app.main import get_database as _get_database
    database = _get_database()
    return database._automation_redis if database else None


@router.get("/api/setpoints")
async def get_all_setpoints(
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get all setpoints for all locations."""
    # This would query all setpoints from database
    # For now, return structure (full implementation would query database)
    return {}


@router.get("/api/setpoints/{location}/{cluster}")
async def get_setpoints(
    location: str,
    cluster: str,
    mode: Optional[str] = Query(None, description="Mode (DAY/NIGHT/TRANSITION). If not specified, returns default/legacy setpoint."),
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get setpoints for a specific location/cluster.
    
    Args:
        location: Location name
        cluster: Cluster name
        mode: Optional mode (DAY/NIGHT/TRANSITION). If None, returns default/legacy setpoint.
    
    Returns:
        Dict with setpoint values including mode and vpd
    """
    setpoints = await database.get_setpoint(location, cluster, mode)
    if not setpoints:
        # Return empty setpoint object instead of 404, so frontend can always display the form
        # This allows the form to show empty fields when no setpoint exists yet
        return {
            'heating_setpoint': None,
            'cooling_setpoint': None,
            'humidity': None,
            'co2': None,
            'vpd': None,
            'mode': mode
        }
    return setpoints


@router.get("/api/setpoints/{location}/{cluster}/all-modes")
async def get_all_setpoints_for_location_cluster(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database)
) -> List[Dict[str, Any]]:
    """Get all setpoints for a location/cluster (all modes).
    
    Args:
        location: Location name
        cluster: Cluster name
    
    Returns:
        List of setpoint dicts, each with mode information
    """
    setpoints = await database.get_all_setpoints_for_location_cluster(location, cluster)
    return setpoints


@router.post("/api/setpoints/{location}/{cluster}")
async def update_setpoints(
    location: str,
    cluster: str,
    setpoints: SetpointUpdate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Update setpoints for a location/cluster.
    
    Validates setpoints, writes to database and Redis with source='api',
    and sets mode to 'auto' (if not in failsafe).
    """
    # Validate mode if provided
    if setpoints.mode:
        valid_modes = ['DAY', 'NIGHT', 'TRANSITION']
        if setpoints.mode.upper() not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {setpoints.mode}. Valid modes: {', '.join(valid_modes)}"
            )
        setpoints.mode = setpoints.mode.upper()
    
    # Validate setpoints if provided
    validation_errors = []
    
    if setpoints.heating_setpoint is not None:
        is_valid, error = validate_setpoint('temperature', setpoints.heating_setpoint, config)
        if not is_valid:
            validation_errors.append(error)
    
    if setpoints.cooling_setpoint is not None:
        is_valid, error = validate_setpoint('temperature', setpoints.cooling_setpoint, config)
        if not is_valid:
            validation_errors.append(error)
    
    if setpoints.humidity is not None:
        is_valid, error = validate_setpoint('humidity', setpoints.humidity, config)
        if not is_valid:
            validation_errors.append(error)
    
    if setpoints.co2 is not None:
        is_valid, error = validate_setpoint('co2', setpoints.co2, config)
        if not is_valid:
            validation_errors.append(error)
    
    if setpoints.vpd is not None:
        is_valid, error = validate_setpoint('vpd', setpoints.vpd, config)
        if not is_valid:
            validation_errors.append(error)
    
    if validation_errors:
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))
    
    # Get existing setpoints to merge (for the specified mode or default)
    existing = await database.get_setpoint(location, cluster, setpoints.mode)
    
    # Merge with existing values
    final_heat = setpoints.heating_setpoint if setpoints.heating_setpoint is not None else (existing.get('heating_setpoint') if existing else None)
    final_cool = setpoints.cooling_setpoint if setpoints.cooling_setpoint is not None else (existing.get('cooling_setpoint') if existing else None)
    final_hum = setpoints.humidity if setpoints.humidity is not None else (existing.get('humidity') if existing else None)
    final_co2 = setpoints.co2 if setpoints.co2 is not None else (existing.get('co2') if existing else None)
    final_vpd = setpoints.vpd if setpoints.vpd is not None else (existing.get('vpd') if existing else None)
    final_ramp_in = setpoints.ramp_in_duration if setpoints.ramp_in_duration is not None else (existing.get('ramp_in_duration') if existing else None)
    
    # Validate ramp_in_duration if provided
    if final_ramp_in is not None and (final_ramp_in < 0 or final_ramp_in > 240):
        raise HTTPException(
            status_code=400,
            detail="ramp_in_duration must be between 0 and 240 minutes"
        )
    
    # VPD ramp warning
    warnings = []
    if final_vpd is not None and final_ramp_in and final_ramp_in > 15:
        warnings.append(f"VPD ramp_in_duration is {final_ramp_in} minutes (>15 min). This may cause stomatal shock, humidity overshoot, or condensation events.")
    
    # Write to database and Redis with source='api'
    try:
        success = await database.set_setpoint(
            location, cluster,
            final_heat,
            final_cool,
            final_hum,
            final_co2,
            final_vpd,
            setpoints.mode,
            final_ramp_in,
            source='api'
        )
        
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update setpoints in database.")
    except ValueError as e:
        logger.error(f"Configuration error in update_setpoints: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Configuration error: {e}")
    except (PostgresConnectionError, PostgresError) as e:
        logger.error(f"Database connection or query error in update_setpoints: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    except Exception as e:
        logger.error(f"Unhandled error updating setpoints: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")
    
    # Set mode to 'auto' if not in failsafe (only for legacy mode=NULL setpoints)
    if setpoints.mode is None:
        automation_redis = database._automation_redis if database else None
        if automation_redis and automation_redis.redis_enabled:
            # Check if in failsafe mode
            failsafe = automation_redis.read_failsafe(location, cluster)
            if not failsafe:
                # Not in failsafe, set mode to 'auto'
                automation_redis.write_mode(location, cluster, 'auto', source='api')
    
    # Log to config_versions for audit trail
    # Calculate changes dictionary with old/new values for fields that changed
    changes = {}
    if setpoints.heating_setpoint is not None:
        old_heat = existing.get('heating_setpoint') if existing else None
        if old_heat != final_heat:
            changes['heating_setpoint'] = {'old': old_heat, 'new': final_heat}
    
    if setpoints.cooling_setpoint is not None:
        old_cool = existing.get('cooling_setpoint') if existing else None
        if old_cool != final_cool:
            changes['cooling_setpoint'] = {'old': old_cool, 'new': final_cool}
    
    if setpoints.humidity is not None:
        old_hum = existing.get('humidity') if existing else None
        if old_hum != final_hum:
            changes['humidity'] = {'old': old_hum, 'new': final_hum}
    
    if setpoints.co2 is not None:
        old_co2 = existing.get('co2') if existing else None
        if old_co2 != final_co2:
            changes['co2'] = {'old': old_co2, 'new': final_co2}
    
    if setpoints.vpd is not None:
        old_vpd = existing.get('vpd') if existing else None
        if old_vpd != final_vpd:
            changes['vpd'] = {'old': old_vpd, 'new': final_vpd}
    
    if setpoints.mode is not None:
        old_mode = existing.get('mode') if existing else None
        if old_mode != setpoints.mode:
            changes['mode'] = {'old': old_mode, 'new': setpoints.mode}
    
    if setpoints.ramp_in_duration is not None:
        old_ramp_in = existing.get('ramp_in_duration') if existing else None
        if old_ramp_in != final_ramp_in:
            changes['ramp_in_duration'] = {'old': old_ramp_in, 'new': final_ramp_in}
    
    # Only log if there were actual changes
    if changes:
        mode_str = f" (mode: {setpoints.mode})" if setpoints.mode else ""
        await database.log_config_version(
            config_type='setpoint',
            author='api',
            comment=f"Setpoint update for {location}/{cluster}{mode_str}",
            location=location,
            cluster=cluster,
            changes=changes
        )
    
    return {
        "location": location,
        "cluster": cluster,
        "mode": setpoints.mode,
        "setpoints": {
            "heating_setpoint": final_heat,
            "cooling_setpoint": final_cool,
            "humidity": final_hum,
            "co2": final_co2,
            "vpd": final_vpd,
            "ramp_in_duration": final_ramp_in
        },
        "warnings": warnings,
        "success": True
    }

