"""Statistics API routes."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from typing import Optional
from app.models import StatisticsResponse
from app.dependencies import get_db_manager

router = APIRouter(prefix="/api/statistics", tags=["statistics"])


@router.get("/{sensor_type}/{location}/{cluster}", response_model=StatisticsResponse)
async def get_statistics(
    sensor_type: str,
    location: str,
    cluster: str,
    start_time: Optional[datetime] = Query(None, alias="start_time"),
    end_time: Optional[datetime] = Query(None, alias="end_time"),
    time_range: Optional[str] = Query("1 Hour", alias="time_range"),
):
    """Get statistics (min/max/avg) for a sensor.
    
    Args:
        sensor_type: Sensor type with cluster suffix (e.g., "dry_bulb_f", "rh_b")
        location: Room name (e.g., "Flower Room")
        cluster: Sensor cluster (e.g., "front", "back", "main")
        start_time: Start timestamp (optional)
        end_time: End timestamp (optional)
        time_range: Time range string if start/end not provided
    """
    db = get_db_manager()
    
    # Calculate time range if not provided
    if start_time is None or end_time is None:
        end_time = datetime.now()
        time_ranges = {
            "1 Minute": 60,
            "5 Minutes": 300,
            "15 Minutes": 900,
            "1 Hour": 3600,
            "6 Hours": 21600,
            "12 Hours": 43200,
            "24 Hours": 86400,
            "7 Days": 604800
        }
        seconds = time_ranges.get(time_range, 3600)
        start_time = end_time - timedelta(seconds=seconds)
    
    # Strip cluster suffix from sensor_type to get base name (like v7)
    # e.g., "dry_bulb_f" -> "dry_bulb", "rh_b" -> "rh"
    import re
    base_sensor_type = re.sub(r'_([fbv])$', '', sensor_type)
    
    statistics = await db.get_statistics(
        base_sensor_type, location, cluster, start_time, end_time
    )
    
    return statistics

