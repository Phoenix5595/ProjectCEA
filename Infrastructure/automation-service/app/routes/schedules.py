"""Schedule management endpoints."""
import logging
from datetime import time as dt_time
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.validation import validate_setpoint

logger = logging.getLogger(__name__)

router = APIRouter()


class ScheduleCreate(BaseModel):
    name: str
    location: str
    cluster: str
    device_name: str
    day_of_week: Optional[int] = None  # 0-6 or None for daily
    start_time: str  # "HH:MM" format
    end_time: str  # "HH:MM" format
    enabled: bool = True
    mode: Optional[str] = None  # DAY, NIGHT, TRANSITION
    target_intensity: Optional[float] = None  # 0-100% for light ramp schedules
    ramp_up_duration: Optional[int] = None  # Minutes to ramp up (0 = instant)
    ramp_down_duration: Optional[int] = None  # Minutes to ramp down (0 = instant)


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    day_of_week: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    enabled: Optional[bool] = None
    mode: Optional[str] = None  # DAY, NIGHT, TRANSITION
    target_intensity: Optional[float] = None  # 0-100% for light ramp schedules
    ramp_up_duration: Optional[int] = None  # Minutes to ramp up (0 = instant)
    ramp_down_duration: Optional[int] = None  # Minutes to ramp down (0 = instant)


# This will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/schedules")
async def get_schedules(
    location: Optional[str] = Query(None),
    cluster: Optional[str] = Query(None),
    database: DatabaseManager = Depends(get_database)
) -> List[Dict[str, Any]]:
    """List all schedules.
    
    Args:
        location: Filter by location (optional)
        cluster: Filter by cluster (optional)
    
    Returns:
        List of schedule dictionaries
    """
    schedules = await database.get_schedules(location, cluster)
    return schedules


@router.post("/api/schedules")
async def create_schedule(
    schedule: ScheduleCreate,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Create a new schedule.
    
    Args:
        schedule: Schedule creation data
    
    Returns:
        Created schedule with ID
    """
    # Validate mode if provided
    if schedule.mode:
        valid_modes = ['DAY', 'NIGHT', 'TRANSITION']
        if schedule.mode.upper() not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {schedule.mode}. Valid modes: {', '.join(valid_modes)}"
            )
        schedule.mode = schedule.mode.upper()
    
    # Validate target_intensity if provided
    if schedule.target_intensity is not None:
        if schedule.target_intensity < 0 or schedule.target_intensity > 100:
            raise HTTPException(
                status_code=400,
                detail="target_intensity must be between 0 and 100"
            )
    
    # Validate ramp durations if provided
    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_up_duration must be >= 0"
        )
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_down_duration must be >= 0"
        )
    
    schedule_id = await database.create_schedule(
        schedule.name,
        schedule.location,
        schedule.cluster,
        schedule.device_name,
        schedule.start_time,
        schedule.end_time,
        schedule.day_of_week,
        schedule.enabled,
        schedule.mode,
        schedule.target_intensity,
        schedule.ramp_up_duration,
        schedule.ramp_down_duration
    )
    
    if not schedule_id:
        raise HTTPException(status_code=500, detail="Failed to create schedule")
    
    # Get created schedule
    schedules = await database.get_schedules(schedule.location, schedule.cluster)
    created = next((s for s in schedules if s['id'] == schedule_id), None)
    
    if not created:
        raise HTTPException(status_code=500, detail="Schedule created but not found")
    
    return created


@router.put("/api/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: int,
    schedule: ScheduleUpdate,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Update a schedule.
    
    Args:
        schedule_id: Schedule ID
        schedule: Schedule update data
    
    Returns:
        Updated schedule
    """
    # Validate mode if provided
    if schedule.mode:
        valid_modes = ['DAY', 'NIGHT', 'TRANSITION']
        if schedule.mode.upper() not in valid_modes:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {schedule.mode}. Valid modes: {', '.join(valid_modes)}"
            )
        schedule.mode = schedule.mode.upper()
    
    # Validate target_intensity if provided
    if schedule.target_intensity is not None:
        if schedule.target_intensity < 0 or schedule.target_intensity > 100:
            raise HTTPException(
                status_code=400,
                detail="target_intensity must be between 0 and 100"
            )
    
    # Validate ramp durations if provided
    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_up_duration must be >= 0"
        )
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_down_duration must be >= 0"
        )
    
    # Get existing schedule to get location/cluster
    all_schedules = await database.get_schedules()
    existing = next((s for s in all_schedules if s['id'] == schedule_id), None)
    
    if not existing:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    success = await database.update_schedule(
        schedule_id,
        schedule.name,
        schedule.start_time,
        schedule.end_time,
        schedule.day_of_week,
        schedule.enabled,
        schedule.mode,
        schedule.target_intensity,
        schedule.ramp_up_duration,
        schedule.ramp_down_duration
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update schedule")
    
    # Get updated schedule
    schedules = await database.get_schedules(existing['location'], existing['cluster'])
    updated = next((s for s in schedules if s['id'] == schedule_id), None)
    
    if not updated:
        raise HTTPException(status_code=500, detail="Schedule updated but not found")
    
    return updated


@router.delete("/api/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: int,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Delete a schedule.
    
    Args:
        schedule_id: Schedule ID
    
    Returns:
        Success confirmation
    """
    success = await database.delete_schedule(schedule_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Schedule not found")
    
    return {
        "id": schedule_id,
        "success": True
    }


class RoomScheduleCreate(BaseModel):
    day_start_time: str
    day_end_time: str
    night_start_time: str
    night_end_time: str
    ramp_up_duration: Optional[int] = None
    ramp_down_duration: Optional[int] = None


@router.get("/api/room-schedule/{location}/{cluster}")
async def get_room_schedule(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get room-level schedule configuration.
    
    First tries to get from config_versions (most recent saved configuration),
    then falls back to inferring from existing device schedules.
    
    Args:
        location: Location name
        cluster: Cluster name
    
    Returns:
        Room schedule configuration
    """
    # First, try to get from schedules table (room_schedule entry)
    pool = await database._get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT start_time, end_time, ramp_up_duration, ramp_down_duration
                FROM schedules
                WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                ORDER BY created_at DESC
                LIMIT 1
            """, location, cluster)
            
            if row:
                # Convert time objects to HH:MM strings
                def format_time(time_val):
                    if isinstance(time_val, str):
                        return time_val
                    if hasattr(time_val, 'hour') and hasattr(time_val, 'minute'):
                        return f"{time_val.hour:02d}:{time_val.minute:02d}"
                    return str(time_val)
                
                day_start = format_time(row['start_time'])
                day_end = format_time(row['end_time'])
                # Night times are inferred from day times (night starts when day ends)
                night_start = day_end
                night_end = day_start
                
                return {
                    "day_start_time": day_start,
                    "day_end_time": day_end,
                    "night_start_time": night_start,
                    "night_end_time": night_end,
                    "ramp_up_duration": row['ramp_up_duration'] if row['ramp_up_duration'] is not None else 30,
                    "ramp_down_duration": row['ramp_down_duration'] if row['ramp_down_duration'] is not None else 15
                }
    except Exception as e:
        logger.warning(f"Error retrieving room schedule from database: {e}. Falling back to inferring from schedules.")
    
    # Fallback: Get all schedules for this room and infer configuration
    # Filter out room_schedule and climate entries
    all_schedules = await database.get_schedules(location, cluster)
    schedules = [s for s in all_schedules if s.get('device_name') not in ['room_schedule', 'climate']]
    
    if not schedules:
        # Return defaults if no schedules exist
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "night_start_time": "20:00",
            "night_end_time": "06:00",
            "ramp_up_duration": 30,
            "ramp_down_duration": 15
        }
    
    # Find a day schedule (target_intensity > 0 or no target_intensity for non-lights)
    # and night schedule (target_intensity == 0)
    day_schedule = None
    night_schedule = None
    
    for schedule in schedules:
        target_intensity = schedule.get('target_intensity')
        mode = (schedule.get('mode') or '').upper()
        
        # Prefer schedules with mode='DAY' or 'NIGHT' for clarity
        if mode == 'DAY' or (target_intensity is not None and target_intensity > 0):
            # This is a day schedule
            if day_schedule is None or mode == 'DAY':
                day_schedule = schedule
        elif mode == 'NIGHT' or (target_intensity is not None and target_intensity == 0):
            # This is a night schedule
            if night_schedule is None or mode == 'NIGHT':
                night_schedule = schedule
    
    # Extract times from schedules
    # Convert time objects to HH:MM strings if needed
    def format_time_value(time_val, default: str) -> str:
        if time_val is None:
            return default
        if isinstance(time_val, str):
            return time_val
        # If it's a time object, convert to HH:MM
        if hasattr(time_val, 'hour') and hasattr(time_val, 'minute'):
            return f"{time_val.hour:02d}:{time_val.minute:02d}"
        # If it's a timedelta or other type, try to convert
        return str(time_val)
    
    def parse_time_to_minutes(time_str: str) -> int:
        """Convert HH:MM string to minutes since midnight."""
        try:
            parts = time_str.split(':')
            return int(parts[0]) * 60 + int(parts[1])
        except:
            return 0
    
    # Get day schedule times
    if day_schedule:
        day_start_raw = format_time_value(day_schedule.get('start_time'), '06:00')
        day_end_raw = format_time_value(day_schedule.get('end_time'), '20:00')
        
        # Check if it's an overnight schedule (end < start in minutes)
        day_start_min = parse_time_to_minutes(day_start_raw)
        day_end_min = parse_time_to_minutes(day_end_raw)
        
        # For day schedule, if it's overnight (e.g., 17:00-11:00), that's actually 18 hours
        # But we want to return the actual start and end times as stored
        day_start = day_start_raw
        day_end = day_end_raw
    else:
        day_start = '06:00'
        day_end = '20:00'
    
    # Get night schedule times
    if night_schedule:
        night_start_raw = format_time_value(night_schedule.get('start_time'), '20:00')
        night_end_raw = format_time_value(night_schedule.get('end_time'), '06:00')
        night_start = night_start_raw
        night_end = night_end_raw
    else:
        # If no night schedule, infer from day schedule
        if day_schedule:
            # Night is the complement of day
            night_start = day_end
            night_end = day_start
        else:
            night_start = '20:00'
            night_end = '06:00'
    
    # Extract ramp durations from day schedule (for lights)
    # ramp_up happens at start of day, ramp_down happens at end of day
    ramp_up = None
    ramp_down = None
    
    if day_schedule:
        ramp_up = day_schedule.get('ramp_up_duration')
        ramp_down = day_schedule.get('ramp_down_duration')
    
    # Fallback to defaults if not found
    if ramp_up is None:
        ramp_up = 30
    if ramp_down is None:
        # Check night schedule for backwards compatibility
        if night_schedule:
            ramp_down = night_schedule.get('ramp_down_duration', 15)
        else:
            ramp_down = 15
    
    return {
        "day_start_time": str(day_start),
        "day_end_time": str(day_end),
        "night_start_time": str(night_start),
        "night_end_time": str(night_end),
        "ramp_up_duration": ramp_up,
        "ramp_down_duration": ramp_down
    }


@router.post("/api/room-schedule/{location}/{cluster}")
async def save_room_schedule(
    location: str,
    cluster: str,
    schedule: RoomScheduleCreate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Save room-level schedule and create schedules for all devices.
    
    Args:
        location: Location name
        cluster: Cluster name
        schedule: Room schedule configuration
    
    Returns:
        Success confirmation with number of schedules created
    """
    # Validate times
    try:
        from datetime import time as dt_time
        day_start_parts = schedule.day_start_time.split(':')
        day_end_parts = schedule.day_end_time.split(':')
        night_start_parts = schedule.night_start_time.split(':')
        night_end_parts = schedule.night_end_time.split(':')
        
        dt_time(int(day_start_parts[0]), int(day_start_parts[1]))
        dt_time(int(day_end_parts[0]), int(day_end_parts[1]))
        dt_time(int(night_start_parts[0]), int(night_start_parts[1]))
        dt_time(int(night_end_parts[0]), int(night_end_parts[1]))
    except (ValueError, IndexError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid time format. Use HH:MM format. Error: {e}"
        )
    
    # Validate ramp durations
    if schedule.ramp_up_duration is not None and schedule.ramp_up_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_up_duration must be >= 0"
        )
    if schedule.ramp_down_duration is not None and schedule.ramp_down_duration < 0:
        raise HTTPException(
            status_code=400,
            detail="ramp_down_duration must be >= 0"
        )
    
    # Validate that day/night times are synchronized (for overnight schedules)
    if schedule.day_end_time != schedule.night_start_time:
        raise HTTPException(
            status_code=400,
            detail=f"day_end_time ({schedule.day_end_time}) must equal night_start_time ({schedule.night_start_time})"
        )
    if schedule.day_start_time != schedule.night_end_time:
        raise HTTPException(
            status_code=400,
            detail=f"day_start_time ({schedule.day_start_time}) must equal night_end_time ({schedule.night_end_time})"
        )
    
    # Get all devices in the room
    devices = config.get_devices()
    room_devices = devices.get(location, {}).get(cluster, {})
    
    if not room_devices:
        raise HTTPException(
            status_code=404,
            detail=f"No devices found for {location}/{cluster}"
        )
    
    # Get existing schedules for this room to delete them (exclude room_schedule and climate entries)
    existing_schedules = await database.get_schedules(location, cluster)
    schedule_ids_to_delete = [s['id'] for s in existing_schedules if s.get('id') and s.get('device_name') not in ['room_schedule', 'climate']]
    
    # Use transaction to ensure atomicity: delete old schedules and create new ones
    pool = await database._get_pool()
    schedules_created = 0
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Delete existing schedules in bulk within transaction (but not room_schedule entry)
                if schedule_ids_to_delete:
                    # Filter out room_schedule entries - we'll update them instead
                    room_schedule_ids = await conn.fetch("""
                        SELECT id FROM schedules
                        WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                    """, location, cluster)
                    room_schedule_id_set = {r['id'] for r in room_schedule_ids}
                    filtered_ids = [sid for sid in schedule_ids_to_delete if sid not in room_schedule_id_set]
                    if filtered_ids:
                        await database.delete_schedules_bulk(filtered_ids, conn)
                        logger.info(f"Deleted {len(filtered_ids)} existing schedules for {location}/{cluster}")
                
                # Save/update room schedule configuration in schedules table
                from datetime import time as dt_time
                day_start_parts = schedule.day_start_time.split(':')
                day_start_time_obj = dt_time(int(day_start_parts[0]), int(day_start_parts[1]))
                day_end_parts = schedule.day_end_time.split(':')
                day_end_time_obj = dt_time(int(day_end_parts[0]), int(day_end_parts[1]))
                
                existing_room_schedule = await conn.fetchrow("""
                    SELECT id FROM schedules
                    WHERE location = $1 AND cluster = $2 AND device_name = 'room_schedule'
                    LIMIT 1
                """, location, cluster)
                
                if existing_room_schedule:
                    # Update existing room schedule
                    await conn.execute("""
                        UPDATE schedules
                        SET start_time = $1, end_time = $2,
                            ramp_up_duration = $3, ramp_down_duration = $4,
                            created_at = NOW()
                        WHERE id = $5
                    """, day_start_time_obj, day_end_time_obj,
                        schedule.ramp_up_duration, schedule.ramp_down_duration,
                        existing_room_schedule['id'])
                else:
                    # Create new room schedule entry
                    await conn.execute("""
                        INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time, enabled, ramp_up_duration, ramp_down_duration)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """, f"Room Schedule {location}/{cluster}", location, cluster, "room_schedule",
                        day_start_time_obj, day_end_time_obj, True,
                        schedule.ramp_up_duration, schedule.ramp_down_duration)
                
                # Create schedules for all devices within transaction
                for device_name, device_info in room_devices.items():
                    device_type = device_info.get('device_type', '')
                    dimming_enabled = device_info.get('dimming_enabled', False)
                    display_name = device_info.get('display_name', device_name)
                    
                    if device_type == 'light' and dimming_enabled:
                        # For lights: Create day schedule with target_intensity and ramp, night schedule with target_intensity=0
                        # Get current intensity (default to 100% if not available)
                        # Note: We'll use 100% as default target for day schedule
                        target_intensity = 100  # Could be made configurable later
                        
                        # Day schedule
                        # ramp_up happens at start of day, ramp_down happens at end of day (when transitioning to night)
                        day_schedule_id = await database.create_schedule(
                            name=f"{display_name} - Day",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.day_start_time,
                            end_time=schedule.day_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode='DAY',
                            target_intensity=target_intensity,
                            ramp_up_duration=schedule.ramp_up_duration or 0,
                            ramp_down_duration=schedule.ramp_down_duration or 0,
                            conn=conn
                        )
                        if day_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create day schedule for {device_name}")
                        
                        # Night schedule
                        # Note: ramp_down_duration should NOT be on night schedule (only on day schedule)
                        night_schedule_id = await database.create_schedule(
                            name=f"{display_name} - Night",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.night_start_time,
                            end_time=schedule.night_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode='NIGHT',
                            target_intensity=0,
                            ramp_up_duration=None,
                            ramp_down_duration=None,  # Fixed: removed ramp_down_duration from night schedule
                            conn=conn
                        )
                        if night_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create night schedule for {device_name}")
                    else:
                        # For other devices: Create ON schedule for day, OFF schedule for night
                        # Day schedule (ON)
                        day_schedule_id = await database.create_schedule(
                            name=f"{display_name} - Day",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.day_start_time,
                            end_time=schedule.day_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode='DAY',
                            target_intensity=None,
                            ramp_up_duration=None,
                            ramp_down_duration=None,
                            conn=conn
                        )
                        if day_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create day schedule for {device_name}")
                        
                        # Night schedule (OFF)
                        night_schedule_id = await database.create_schedule(
                            name=f"{display_name} - Night",
                            location=location,
                            cluster=cluster,
                            device_name=device_name,
                            start_time=schedule.night_start_time,
                            end_time=schedule.night_end_time,
                            day_of_week=None,
                            enabled=True,
                            mode='NIGHT',
                            target_intensity=None,
                            ramp_up_duration=None,
                            ramp_down_duration=None,
                            conn=conn
                        )
                        if night_schedule_id:
                            schedules_created += 1
                        else:
                            raise RuntimeError(f"Failed to create night schedule for {device_name}")
                
                logger.info(f"Successfully created {schedules_created} schedules for {location}/{cluster} in transaction")
    except Exception as e:
        logger.error(f"Error saving room schedule for {location}/{cluster}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Database transaction failed: {str(e)}"
        )
    
    # Log room schedule configuration to database (not Redis)
    await database.log_config_version(
        config_type='room_schedule',
        author='system',  # Could be enhanced to accept user info
        comment=f"Room schedule updated for {location}/{cluster}",
        location=location,
        cluster=cluster,
        changes={
            'day_start_time': schedule.day_start_time,
            'day_end_time': schedule.day_end_time,
            'night_start_time': schedule.night_start_time,
            'night_end_time': schedule.night_end_time,
            'ramp_up_duration': schedule.ramp_up_duration,
            'ramp_down_duration': schedule.ramp_down_duration,
            'schedules_created': schedules_created,
            'devices_configured': len(room_devices)
        }
    )
    
    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "schedules_created": schedules_created,
        "devices_configured": len(room_devices)
    }


class ClimateScheduleSetpoint(BaseModel):
    """Setpoint data for a specific mode."""
    heating_setpoint: Optional[float] = None
    cooling_setpoint: Optional[float] = None
    humidity: Optional[float] = None
    co2: Optional[float] = None
    vpd: Optional[float] = None
    ramp_in_duration: Optional[int] = None  # Minutes to ramp in when entering this mode (0 = instant)


class ClimateScheduleCreate(BaseModel):
    """Climate schedule with pre-day/pre-night durations and setpoints for all modes."""
    day_start_time: str  # "HH:MM" format (from light schedule)
    day_end_time: str  # "HH:MM" format (from light schedule)
    pre_day_duration: int  # Minutes before day starts
    pre_night_duration: int  # Minutes after night starts
    setpoints: Dict[str, ClimateScheduleSetpoint]  # Keys: 'DAY', 'NIGHT', 'PRE_DAY', 'PRE_NIGHT'


@router.get("/api/climate-schedule/{location}/{cluster}")
async def get_climate_schedule(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get climate schedule and all setpoints for a location/cluster.
    
    Args:
        location: Location name
        cluster: Cluster name
    
    Returns:
        Dict with day_start_time, day_end_time, pre_day_duration, pre_night_duration,
        and setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)
    """
    # Get light schedule for day times
    light_schedule = await database.get_light_schedule(location, cluster)
    if not light_schedule:
        # Return defaults if no light schedule found
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "pre_day_duration": 0,
            "pre_night_duration": 0,
            "setpoints": {
                "DAY": {},
                "NIGHT": {},
                "PRE_DAY": {},
                "PRE_NIGHT": {}
            }
        }
    
    # Get climate schedule (pre-day/pre-night durations)
    climate_schedule = await database.get_climate_schedule(location, cluster)
    
    # Get setpoints for all modes
    setpoints = {}
    for mode in ['DAY', 'NIGHT', 'PRE_DAY', 'PRE_NIGHT']:
        setpoint_data = await database.get_setpoint(location, cluster, mode)
        if setpoint_data:
            setpoints[mode] = {
                "heating_setpoint": setpoint_data.get('heating_setpoint'),
                "cooling_setpoint": setpoint_data.get('cooling_setpoint'),
                "humidity": setpoint_data.get('humidity'),
                "co2": setpoint_data.get('co2'),
                "vpd": setpoint_data.get('vpd'),
                "ramp_in_duration": setpoint_data.get('ramp_in_duration', 0) or 0
            }
        else:
            setpoints[mode] = {}
    
    return {
        "day_start_time": light_schedule.get('day_start_time'),
        "day_end_time": light_schedule.get('day_end_time'),
        "pre_day_duration": climate_schedule.get('pre_day_duration', 0) if climate_schedule else 0,
        "pre_night_duration": climate_schedule.get('pre_night_duration', 0) if climate_schedule else 0,
        "setpoints": setpoints
    }


@router.post("/api/climate-schedule/{location}/{cluster}")
async def save_climate_schedule(
    location: str,
    cluster: str,
    schedule: ClimateScheduleCreate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Save climate schedule and setpoints atomically.
    
    This endpoint atomically saves:
    - Climate schedule (pre_day_duration, pre_night_duration)
    - Setpoints for all modes (DAY, NIGHT, PRE_DAY, PRE_NIGHT)
    
    Args:
        location: Location name
        cluster: Cluster name
        schedule: Climate schedule data
    
    Returns:
        Success response with warnings (if any)
    """
    from app.control.scheduler import Scheduler
    
    # Validate conflict rules using scheduler
    scheduler = Scheduler([])
    is_valid, error_msg = scheduler.validate_climate_schedule_conflicts(
        schedule.day_start_time,
        schedule.day_end_time,
        schedule.pre_day_duration,
        schedule.pre_night_duration
    )
    
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=error_msg
        )
    
    # Validate durations
    if schedule.pre_day_duration < 0 or schedule.pre_day_duration > 240:
        raise HTTPException(
            status_code=400,
            detail=f"pre_day_duration must be between 0 and 240 minutes"
        )
    
    if schedule.pre_night_duration < 0 or schedule.pre_night_duration > 240:
        raise HTTPException(
            status_code=400,
            detail=f"pre_night_duration must be between 0 and 240 minutes"
        )
    
    warnings = []
    
    # Validate setpoints and check for VPD ramp warnings
    for mode, setpoint_data in schedule.setpoints.items():
        if mode not in ['DAY', 'NIGHT', 'PRE_DAY', 'PRE_NIGHT']:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode in setpoints: {mode}. Valid modes: DAY, NIGHT, PRE_DAY, PRE_NIGHT"
            )
        
        # Validate ramp_in_duration
        ramp_in = setpoint_data.ramp_in_duration or 0
        if ramp_in < 0 or ramp_in > 240:
            raise HTTPException(
                status_code=400,
                detail=f"ramp_in_duration for {mode} must be between 0 and 240 minutes"
            )
        
        # VPD ramp warning
        if setpoint_data.vpd is not None and ramp_in > 15:
            warnings.append(f"VPD ramp_in_duration for {mode} is {ramp_in} minutes (>15 min). This may cause stomatal shock, humidity overshoot, or condensation events.")
        
        # Validate setpoint values if provided
        if setpoint_data.heating_setpoint is not None:
            is_valid, error = validate_setpoint('temperature', setpoint_data.heating_setpoint, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.heating_setpoint: {error}")
        
        if setpoint_data.cooling_setpoint is not None:
            is_valid, error = validate_setpoint('temperature', setpoint_data.cooling_setpoint, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.cooling_setpoint: {error}")
        
        if setpoint_data.humidity is not None:
            is_valid, error = validate_setpoint('humidity', setpoint_data.humidity, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.humidity: {error}")
        
        if setpoint_data.co2 is not None:
            is_valid, error = validate_setpoint('co2', setpoint_data.co2, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.co2: {error}")
        
        if setpoint_data.vpd is not None:
            is_valid, error = validate_setpoint('vpd', setpoint_data.vpd, config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=f"{mode}.vpd: {error}")
    
    # Atomic save: use database transaction
    try:
        pool = await database._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Save climate schedule (update or create schedule with pre_day_duration/pre_night_duration)
                # Find existing climate schedule or create new one
                existing = await conn.fetchrow("""
                    SELECT id FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND (pre_day_duration IS NOT NULL OR pre_night_duration IS NOT NULL)
                    LIMIT 1
                """, location, cluster)
                
                if existing:
                    # Update existing
                    await conn.execute("""
                        UPDATE schedules
                        SET pre_day_duration = $1, pre_night_duration = $2
                        WHERE id = $3
                    """, schedule.pre_day_duration, schedule.pre_night_duration, existing['id'])
                else:
                    # Create new (use a dummy device_name for climate schedules)
                    # Convert time strings to TIME objects (handle both "HH:MM" and "HH:MM:SS" formats)
                    start_parts = schedule.day_start_time.split(':')
                    end_parts = schedule.day_end_time.split(':')
                    start_time_obj = dt_time(int(start_parts[0]), int(start_parts[1]), int(start_parts[2]) if len(start_parts) > 2 else 0)
                    end_time_obj = dt_time(int(end_parts[0]), int(end_parts[1]), int(end_parts[2]) if len(end_parts) > 2 else 0)
                    
                    await conn.execute("""
                        INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time, enabled, pre_day_duration, pre_night_duration)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """, f"Climate Schedule {location}/{cluster}", location, cluster, "climate", 
                        start_time_obj, end_time_obj, True,
                        schedule.pre_day_duration, schedule.pre_night_duration)
                
                # Save setpoints for all modes
                for mode, setpoint_data in schedule.setpoints.items():
                    # Check if setpoint exists
                    existing_setpoint = await conn.fetchrow("""
                        SELECT id FROM setpoints
                        WHERE location = $1 AND cluster = $2 AND mode = $3
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """, location, cluster, mode)
                    
                    # Build update dict
                    updates = {}
                    if setpoint_data.heating_setpoint is not None:
                        updates['heating_setpoint'] = setpoint_data.heating_setpoint
                    if setpoint_data.cooling_setpoint is not None:
                        updates['cooling_setpoint'] = setpoint_data.cooling_setpoint
                    if setpoint_data.humidity is not None:
                        updates['humidity'] = setpoint_data.humidity
                    if setpoint_data.co2 is not None:
                        updates['co2'] = setpoint_data.co2
                    if setpoint_data.vpd is not None:
                        updates['vpd'] = setpoint_data.vpd
                    if setpoint_data.ramp_in_duration is not None:
                        updates['ramp_in_duration'] = setpoint_data.ramp_in_duration
                    
                    if updates:
                        if existing_setpoint:
                            # Update existing
                            set_clauses = [f"{k} = ${i+1}" for i, k in enumerate(updates.keys())]
                            values = list(updates.values()) + [existing_setpoint['id']]
                            await conn.execute(f"""
                                UPDATE setpoints
                                SET {', '.join(set_clauses)}, updated_at = NOW()
                                WHERE id = ${len(updates) + 1}
                            """, *values)
                        else:
                            # Insert new
                            columns = ['location', 'cluster', 'mode'] + list(updates.keys())
                            placeholders = [f"${i+1}" for i in range(len(columns))]
                            values = [location, cluster, mode] + list(updates.values())
                            await conn.execute(f"""
                                INSERT INTO setpoints ({', '.join(columns)}, updated_at)
                                VALUES ({', '.join(placeholders)}, NOW())
                            """, *values)
    
    except Exception as e:
        logger.error(f"Error saving climate schedule: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save climate schedule: {str(e)}"
        )
    
    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "warnings": warnings
    }

