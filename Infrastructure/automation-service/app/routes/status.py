"""Status and health check endpoints."""
from fastapi import APIRouter, Depends
from datetime import datetime
from typing import Dict, Any, Optional
from app.database import DatabaseManager
from app.control.relay_manager import RelayManager
from app.config import ConfigLoader

router = APIRouter()


# These will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/api/status")
async def get_status(
    database: DatabaseManager = Depends(get_database),
    relay_manager: RelayManager = Depends(get_relay_manager),
    config: ConfigLoader = Depends(get_config)
):
    """Get full system status."""
    # Get all device states
    devices = {}
    device_states = relay_manager.get_all_states()
    
    device_config = config.get_devices()
    for location, clusters in device_config.items():
        devices[location] = {}
        for cluster, cluster_devices in clusters.items():
            devices[location][cluster] = {}
            for device_name in cluster_devices.keys():
                key = (location, cluster, device_name)
                state = device_states.get(key, 0)
                mode = relay_manager.get_device_mode(location, cluster, device_name) or 'auto'
                channel = relay_manager.get_channel(location, cluster, device_name)
                
                devices[location][cluster][device_name] = {
                    "state": state,
                    "mode": mode,
                    "channel": channel
                }
    
    # Get sensor values
    sensors = {}
    sensor_mapping = config.get_sensor_mapping()
    for location, clusters in sensor_mapping.items():
        sensors[location] = {}
        for cluster, cluster_sensors in clusters.items():
            sensors[location][cluster] = {}
            for sensor_type, sensor_name in cluster_sensors.items():
                value = await database.get_sensor_value(sensor_name)
                sensors[location][cluster][sensor_type] = value
    
    return {
        "devices": devices,
        "sensors": sensors,
        "timestamp": datetime.now().isoformat()
    }

