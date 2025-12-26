"""Schedule management endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.database import DatabaseManager
from app.config import ConfigLoader

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
    
    Infers room schedule from existing device schedules in the room.
    
    Args:
        location: Location name
        cluster: Cluster name
    
    Returns:
        Room schedule configuration
    """
    # Get all schedules for this room
    schedules = await database.get_schedules(location, cluster)
    
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
        mode = schedule.get('mode', '').upper()
        
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
    
    # Get existing schedules for this room to delete them
    existing_schedules = await database.get_schedules(location, cluster)
    schedule_ids_to_delete = [s['id'] for s in existing_schedules if s.get('id')]
    
    # Use transaction to ensure atomicity: delete old schedules and create new ones
    pool = await database._get_pool()
    schedules_created = 0
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Delete existing schedules in bulk within transaction
                if schedule_ids_to_delete:
                    await database.delete_schedules_bulk(schedule_ids_to_delete, conn)
                    logger.info(f"Deleted {len(schedule_ids_to_delete)} existing schedules for {location}/{cluster}")
                
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

