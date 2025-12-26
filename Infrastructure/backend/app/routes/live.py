"""Live snapshot API routes for Grafana."""
from datetime import datetime
from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Tuple
from app.models import LiveSnapshotResponse, LiveSensorValue
from app.redis_client import get_all_sensor_values, get_all_sensor_timestamps
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])

# Stale threshold in seconds (sensors older than this are marked stale)
STALE_THRESHOLD_SECONDS = 30.0

# Unit mapping based on sensor name patterns
UNIT_MAP = {
    "temp": "°C",
    "bulb": "°C",
    "co2": "ppm",
    "rh": "%",
    "vpd": "kPa",
    "pressure": "hPa",
    "water_level": "mm"
}

# Sensor name to location/cluster mapping
# Maps sensor suffix to (location, cluster)
SENSOR_LOCATION_MAP = {
    "_b": ("Flower Room", "back"),
    "_f": ("Flower Room", "front"),
    "_v": ("Veg Room", "main"),
    "": ("Lab", "main")  # Sensors without suffix (like lab_temp)
}


def get_location_cluster_from_sensor(sensor_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract location and cluster from sensor name.
    
    Args:
        sensor_name: Sensor name (e.g., 'dry_bulb_b', 'co2_f', 'lab_temp')
    
    Returns:
        Tuple of (location, cluster) or (None, None) if unknown
    """
    # Check for known suffixes first (most specific)
    if sensor_name.endswith("_b"):
        return "Flower Room", "back"
    elif sensor_name.endswith("_f"):
        return "Flower Room", "front"
    elif sensor_name.endswith("_v"):
        return "Veg Room", "main"
    
    # Check for lab sensors (no location suffix, contains "lab")
    if "lab" in sensor_name.lower():
        return "Lab", "main"
    
    # Unknown sensor - return None
    return None, None


def get_unit_from_sensor_name(sensor_name: str) -> str:
    """Determine unit from sensor name.
    
    Args:
        sensor_name: Sensor name
    
    Returns:
        Unit string (default: "")
    """
    for key, unit in UNIT_MAP.items():
        if key in sensor_name:
            return unit
    return ""


def filter_by_cluster_location(
    values: Dict[str, LiveSensorValue],
    cluster: Optional[str] = None,
    location: Optional[str] = None
) -> Dict[str, LiveSensorValue]:
    """Filter sensor values by cluster and/or location.
    
    Args:
        values: Dictionary of sensor values
        cluster: Optional cluster filter
        location: Optional location filter
    
    Returns:
        Filtered dictionary
    """
    if not cluster and not location:
        return values
    
    filtered = {}
    for sensor_name, sensor_value in values.items():
        if cluster and sensor_value.cluster != cluster:
            continue
        if location and sensor_value.location != location:
            continue
        filtered[sensor_name] = sensor_value
    
    return filtered


async def build_live_snapshot(
    cluster: Optional[str] = None,
    location: Optional[str] = None
) -> LiveSnapshotResponse:
    """Build live snapshot from Redis.
    
    Args:
        cluster: Optional cluster filter
        location: Optional location filter
    
    Returns:
        LiveSnapshotResponse with all sensor values
    
    Raises:
        HTTPException: If Redis is unavailable
    """
    # Get all sensor values from Redis (single query)
    sensor_values = await get_all_sensor_values()
    
    if sensor_values is None or len(sensor_values) == 0:
        # Check if Redis is available
        from app.redis_client import get_redis_client
        client = await get_redis_client()
        if not client:
            raise HTTPException(
                status_code=503,
                detail="Redis unavailable. Live sensor data cannot be retrieved."
            )
        # Redis is available but no data
        sensor_values = {}
    
    # Get all timestamps in batch (single query)
    sensor_names = list(sensor_values.keys())
    timestamps_ms = await get_all_sensor_timestamps(sensor_names)
    
    # Use current time as snapshot timestamp (consistent across all sensors)
    snapshot_ts = int(datetime.now().timestamp())
    snapshot_ts_iso = datetime.fromtimestamp(snapshot_ts).isoformat() + "Z"
    
    # Build sensor value objects
    live_values: Dict[str, LiveSensorValue] = {}
    
    for sensor_name, value in sensor_values.items():
        # Get timestamp
        ts_ms = timestamps_ms.get(sensor_name)
        if ts_ms:
            sensor_ts = ts_ms / 1000.0  # Convert to seconds
            age_seconds = snapshot_ts - sensor_ts
            stale = age_seconds > STALE_THRESHOLD_SECONDS
        else:
            age_seconds = None
            stale = True
        
        # Get location and cluster
        loc, clus = get_location_cluster_from_sensor(sensor_name)
        
        # Get unit
        unit = get_unit_from_sensor_name(sensor_name)
        
        # Create sensor value object
        live_values[sensor_name] = LiveSensorValue(
            value=value,
            unit=unit,
            sensor=sensor_name,
            location=loc,
            cluster=clus,
            stale=stale,
            age_seconds=age_seconds
        )
    
    # Filter by cluster/location if specified
    if cluster or location:
        live_values = filter_by_cluster_location(live_values, cluster, location)
    
    return LiveSnapshotResponse(
        ts=snapshot_ts,
        ts_iso=snapshot_ts_iso,
        values=live_values
    )


@router.get("", response_model=LiveSnapshotResponse)
async def get_live_snapshot():
    """Get live snapshot of all sensors.
    
    Returns a coherent snapshot with consistent timestamp for all sensors.
    All values are read from Redis in a single batch operation.
    
    Returns:
        LiveSnapshotResponse with all sensor values
    """
    return await build_live_snapshot()


@router.get("/{cluster}", response_model=LiveSnapshotResponse)
async def get_live_snapshot_by_cluster(cluster: str):
    """Get live snapshot for a specific cluster.
    
    Args:
        cluster: Cluster name (e.g., "back", "front", "main")
    
    Returns:
        LiveSnapshotResponse filtered by cluster
    """
    return await build_live_snapshot(cluster=cluster)


@router.get("/{cluster}/{location}", response_model=LiveSnapshotResponse)
async def get_live_snapshot_by_cluster_location(cluster: str, location: str):
    """Get live snapshot for a specific cluster and location.
    
    Args:
        cluster: Cluster name (e.g., "back", "front", "main")
        location: Location name (e.g., "Flower Room", "Veg Room")
    
    Returns:
        LiveSnapshotResponse filtered by cluster and location
    
    Raises:
        HTTPException: If no sensors match the filter
    """
    result = await build_live_snapshot(cluster=cluster, location=location)
    
    if not result.values:
        raise HTTPException(
            status_code=404,
            detail=f"No sensors found for cluster '{cluster}' and location '{location}'"
        )
    
    return result

