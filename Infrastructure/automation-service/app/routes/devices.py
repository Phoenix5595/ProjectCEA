"""Device control endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.validation import validate_device_mapping

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

