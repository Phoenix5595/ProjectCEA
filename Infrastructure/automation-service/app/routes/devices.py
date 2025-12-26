"""Device control endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import yaml
import logging
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.validation import validate_device_mapping

logger = logging.getLogger(__name__)

router = APIRouter()


class DeviceControlRequest(BaseModel):
    state: int  # 0 = OFF, 1 = ON
    reason: Optional[str] = "Manual override"


class DeviceModeRequest(BaseModel):
    mode: str  # 'manual', 'auto', 'scheduled'


class DeviceMappingUpdate(BaseModel):
    channel: int
    active_high: bool = True
    safe_state: int = 0
    mcp_board_id: Optional[int] = None


class DeviceConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    device_type: Optional[str] = None


# These will be overridden by main app
def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    from app.main import get_config as _get_config
    return _get_config()


@router.get("/api/devices")
async def get_all_devices(
    relay_manager: RelayManager = Depends(get_relay_manager)
) -> List[Dict[str, Any]]:
    """Get all devices with current state."""
    devices = []
    device_states = relay_manager.get_all_states()
    
    for (location, cluster, device_name), state in device_states.items():
        mode = relay_manager.get_device_mode(location, cluster, device_name) or 'auto'
        channel = relay_manager.get_channel(location, cluster, device_name)
        
        devices.append({
            "location": location,
            "cluster": cluster,
            "device_name": device_name,
            "state": state,
            "mode": mode,
            "channel": channel
        })
    
    return devices


@router.get("/api/devices/{location}/{cluster}")
async def get_devices_for_location_cluster(
    location: str,
    cluster: str,
    relay_manager: RelayManager = Depends(get_relay_manager),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Get devices for a specific location/cluster with configuration."""
    devices = {}
    device_states = relay_manager.get_all_states()
    device_configs = config.get_devices()
    
    # Get all devices from config for this location/cluster
    location_config = device_configs.get(location, {})
    cluster_config = location_config.get(cluster, {})
    
    # Iterate over all devices in config (not just those with states)
    for device_name, device_info in cluster_config.items():
        # Get state from relay manager (default to 0 if not found)
        key = (location, cluster, device_name)
        state = device_states.get(key, 0)
        mode = relay_manager.get_device_mode(location, cluster, device_name) or 'auto'
        channel = relay_manager.get_channel(location, cluster, device_name)
        
        devices[device_name] = {
                "state": state,
                "mode": mode,
            "channel": channel,
            "device_type": device_info.get("device_type"),
            "display_name": device_info.get("display_name"),
            "dimming_enabled": device_info.get("dimming_enabled", False),
            "dimming_type": device_info.get("dimming_type"),
            "dimming_board_id": device_info.get("dimming_board_id"),
            "dimming_channel": device_info.get("dimming_channel")
            }
    
    return {"location": location, "cluster": cluster, "devices": devices}


@router.get("/api/devices/{location}/{cluster}/{device}")
async def get_device_details(
    location: str,
    cluster: str,
    device: str,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get detailed device status."""
    state = relay_manager.get_device_state(location, cluster, device)
    mode = relay_manager.get_device_mode(location, cluster, device) or 'auto'
    channel = relay_manager.get_channel(location, cluster, device)
    device_info = relay_manager.get_device_info(location, cluster, device)
    
    if state is None:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Get device state from database
    db_state = await database.get_device_state(location, cluster, device)
    
    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        "state": state,
        "mode": mode,
        "channel": channel,
        "device_info": device_info,
        "database_state": db_state
    }


@router.post("/api/devices/{location}/{cluster}/{device}/control")
async def control_device(
    location: str,
    cluster: str,
    device: str,
    request: DeviceControlRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Manually control a device (turn ON/OFF)."""
    if request.state not in [0, 1]:
        raise HTTPException(status_code=400, detail="State must be 0 (OFF) or 1 (ON)")
    
    current_state = relay_manager.get_device_state(location, cluster, device) or 0
    channel = relay_manager.get_channel(location, cluster, device)
    
    if channel is None:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Set device state
    success, reason = relay_manager.set_device_state(
        location, cluster, device, request.state, 'manual'
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=reason or "Failed to set device state")
    
    # Update database
    await database.set_device_state(location, cluster, device, channel, request.state, 'manual')
    await database.log_control_action(
        location, cluster, device, channel,
        current_state, request.state, 'manual',
        request.reason or "Manual override"
    )
    
    return {
        "location": location,
        "cluster": cluster,
        "device": device,
        "state": request.state,
        "mode": "manual",
        "success": True
    }


@router.post("/api/devices/{location}/{cluster}/{device}/mode")
async def set_device_mode(
    location: str,
    cluster: str,
    device: str,
    request: DeviceModeRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Set device control mode."""
    if request.mode not in ['manual', 'auto', 'scheduled']:
        raise HTTPException(status_code=400, detail="Mode must be 'manual', 'auto', or 'scheduled'")
    
    current_state = relay_manager.get_device_state(location, cluster, device)
    channel = relay_manager.get_channel(location, cluster, device)
    
    if channel is None:
        raise HTTPException(status_code=404, detail="Device not found")
    
    # Update mode in database
    state = current_state or 0
    await database.set_device_state(location, cluster, device, channel, state, request.mode)
    
    return {
        "location": location,
        "cluster": cluster,
        "device": device,
        "mode": request.mode,
        "success": True
    }


@router.get("/api/control/history")
async def get_control_history(
    location: Optional[str] = None,
    cluster: Optional[str] = None,
    device: Optional[str] = None,
    limit: int = 100,
    database: DatabaseManager = Depends(get_database)
) -> List[Dict[str, Any]]:
    """Get control history with optional filters."""
    # This would query control_history table
    # For now, return empty list (full implementation would query database)
    return []


@router.get("/api/devices/mappings")
async def get_all_device_mappings(
    database: DatabaseManager = Depends(get_database)
) -> List[Dict[str, Any]]:
    """Get all device mappings.
    
    Returns:
        List of device mapping dicts with location, cluster, device_name, channel, active_high, safe_state, mcp_board_id
    """
    return await database.get_all_device_mappings()


@router.get("/api/devices/{location}/{cluster}/{device}/mapping")
async def get_device_mapping(
    location: str,
    cluster: str,
    device: str,
    database: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get device mapping for a specific device.
    
    Returns:
        Device mapping dict with channel, active_high, safe_state, mcp_board_id, updated_at
    """
    mapping = await database.get_device_mapping(location, cluster, device)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Device mapping not found")
    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        **mapping
    }


@router.post("/api/devices/{location}/{cluster}/{device}/mapping")
async def update_device_mapping(
    location: str,
    cluster: str,
    device: str,
    mapping: DeviceMappingUpdate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Update device mapping for a device.
    
    Backend validates and persists all device mappings.
    Node-RED can only edit mappings via this API, not directly.
    
    Returns:
        Updated device mapping
    """
    # Validate mapping
    # Get existing mappings to check for duplicates
    existing_mappings_list = await database.get_all_device_mappings()
    existing_mappings = {}
    for m in existing_mappings_list:
        key = (m['location'], m['cluster'], m['device_name'])
        if key != (location, cluster, device):  # Exclude current device
            existing_mappings[key] = m
    
    is_valid, error_message = validate_device_mapping(
        mapping.channel,
        mapping.mcp_board_id,
        config,
        existing_mappings
    )
    
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message or "Invalid device mapping")
    
    # Validate safe_state
    if mapping.safe_state not in [0, 1]:
        raise HTTPException(status_code=400, detail="safe_state must be 0 or 1")
    
    # Update mapping in database
    success = await database.set_device_mapping(
        location,
        cluster,
        device,
        mapping.channel,
        mapping.active_high,
        mapping.safe_state,
        mapping.mcp_board_id
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update device mapping")
    
    # Return updated mapping
    updated = await database.get_device_mapping(location, cluster, device)
    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        **updated
    }


@router.post("/api/devices/{location}/{cluster}/{device}/config")
async def update_device_config(
    location: str,
    cluster: str,
    device: str,
    config_update: DeviceConfigUpdate,
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Update device configuration (display_name, device_type).
    
    Args:
        location: Location name
        cluster: Cluster name
        device: Device name
        config_update: Configuration update request
    
    Returns:
        Updated device configuration
    """
    # Validate device exists
    device_configs = config.get_devices()
    if location not in device_configs:
        raise HTTPException(status_code=404, detail=f"Location {location} not found")
    if cluster not in device_configs.get(location, {}):
        raise HTTPException(status_code=404, detail=f"Cluster {cluster} not found in {location}")
    if device not in device_configs[location][cluster]:
        raise HTTPException(status_code=404, detail=f"Device {device} not found in {location}/{cluster}")
    
    # Validate device_type if provided
    if config_update.device_type is not None:
        valid_types = ['heater', 'fan', 'dehumidifier', 'humidifier', 'light', 'pump', 'co2', 'vent']
        if config_update.device_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid device_type. Must be one of: {', '.join(valid_types)}"
            )
    
    # Update config
    success = config.update_device_config(
        location,
        cluster,
        device,
        display_name=config_update.display_name,
        device_type=config_update.device_type
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update device configuration")
    
    # Reload config to get updated values
    config.reload()
    
    # Get fresh device configs after reload
    device_configs = config.get_devices()
    
    # Return updated device info
    device_info = device_configs[location][cluster][device]
    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        "display_name": device_info.get("display_name"),
        "device_type": device_info.get("device_type"),
        "success": True
    }


@router.get("/api/devices/channels")
async def get_all_channels(
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Get all 16 MCP channels (0-15) with their current device assignments.
    
    Returns:
        Dict with channel numbers as keys and device info as values
    """
    channels = {}
    device_configs = config.get_devices()
    
    # Initialize all 16 channels as empty
    for channel in range(16):
        channels[str(channel)] = {
            "channel": channel,
            "device_name": None,
            "display_name": None,
            "device_type": None,
            "location": None,
            "cluster": None,
            "light_name": None  # If device_type is light, this is the display_name
        }
    
    # Get all light names from config
    light_names = []
    for location, clusters in device_configs.items():
        for cluster, devices in clusters.items():
            for device_name, device_info in devices.items():
                if device_info.get("device_type") == "light":
                    display_name = device_info.get("display_name")
                    if display_name:
                        light_names.append({
                            "name": display_name,
                            "device_name": device_name,
                            "location": location,
                            "cluster": cluster
                        })
    
    # Populate channels with existing devices
    for location, clusters in device_configs.items():
        for cluster, devices in clusters.items():
            for device_name, device_info in devices.items():
                channel = device_info.get("channel")
                if channel is not None and 0 <= channel < 16:
                    device_type = device_info.get("device_type")
                    display_name = device_info.get("display_name")
                    
                    channels[str(channel)] = {
                        "channel": channel,
                        "device_name": device_name,
                        "display_name": display_name,
                        "device_type": device_type,
                        "location": location,
                        "cluster": cluster,
                        "light_name": display_name if device_type == "light" else None
                    }
    
    return {
        "channels": channels,
        "light_names": light_names
    }


class ChannelDeviceUpdate(BaseModel):
    device_name: str
    device_type: str
    location: str
    cluster: str
    light_name: Optional[str] = None  # If device_type is "light", specify which light


@router.post("/api/devices/channels/{channel}")
async def update_channel_device(
    channel: int,
    update: ChannelDeviceUpdate,
    config: ConfigLoader = Depends(get_config)
) -> Dict[str, Any]:
    """Update or create a device for a specific MCP channel.
    
    Args:
        channel: MCP channel number (0-15)
        update: Device update request
    
    Returns:
        Updated channel device info
    """
    if channel < 0 or channel > 15:
        raise HTTPException(status_code=400, detail="Channel must be between 0 and 15")
    
    # Validate device_type
    valid_types = ['heater', 'dehumidifier', 'extraction fan', 'fan', 'humidifier', 'co2 tank', 'light']
    if update.device_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid device_type. Must be one of: {', '.join(valid_types)}"
        )
    
    # Normalize device_type for config (remove spaces, handle special cases)
    normalized_type = update.device_type.replace(' ', '_')
    if normalized_type == 'co2_tank':
        normalized_type = 'co2'
    elif normalized_type == 'extraction_fan':
        normalized_type = 'fan'
    
    # Get the full config
    full_config = config._config
    
    # Ensure devices structure exists
    if 'devices' not in full_config:
        full_config['devices'] = {}
    if update.location not in full_config['devices']:
        full_config['devices'][update.location] = {}
    if update.cluster not in full_config['devices'][update.location]:
        full_config['devices'][update.location][update.cluster] = {}
    
    device_configs = full_config['devices']
    
    # Remove device from old channel if it exists (same channel, different device)
    for loc, clusters in device_configs.items():
        for clust, devices in clusters.items():
            for dev_name, dev_info in list(devices.items()):
                if dev_info.get("channel") == channel and (loc != update.location or clust != update.cluster or dev_name != update.device_name):
                    # Remove old device if channel is being reassigned
                    del devices[dev_name]
    
    # Create or update device
    device_info = {
        "channel": channel,
        "device_type": normalized_type
    }
    
    # If it's a light and light_name is provided, set display_name
    if update.device_type == "light" and update.light_name:
        device_info["display_name"] = update.light_name
    elif update.device_type == "light":
        # Try to find existing light with this name
        device_info["display_name"] = update.device_name
    
    # Set default values for lights
    if normalized_type == "light":
        device_info["pid_enabled"] = False
        device_info["interlock_with"] = []
        device_info["dimming_enabled"] = True
        device_info["dimming_type"] = "dfr0971"
    
    device_configs[update.location][update.cluster][update.device_name] = device_info
    
    # Write back to YAML
    try:
        with open(config.config_path, 'w') as f:
            yaml.dump(full_config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        config.reload()
    except Exception as e:
        logger.error(f"Error writing config: {e}")
        raise HTTPException(status_code=500, detail="Failed to update configuration file")
    
    return {
        "channel": channel,
        "device_name": update.device_name,
        "display_name": device_info.get("display_name"),
        "device_type": normalized_type,
        "location": update.location,
        "cluster": update.cluster,
        "success": True
    }

