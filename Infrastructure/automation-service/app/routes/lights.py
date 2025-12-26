"""Light dimming control endpoints for DFR0971 DAC modules."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, Optional

router = APIRouter()


class IntensityControl(BaseModel):
    """Intensity control request."""
    intensity: float  # 0-100%
    duration: Optional[float] = None  # Optional ramp duration (for future use)


class VoltageControl(BaseModel):
    """Voltage control request."""
    voltage: float  # 0-10V
    duration: Optional[float] = None  # Optional ramp duration (for future use)


# These will be overridden by main app
def get_dfr0971_manager():
    """Dependency to get DFR0971 manager."""
    raise RuntimeError("Dependency not injected")


def get_config():
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager():
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_interlock_manager():
    """Dependency to get interlock manager."""
    raise RuntimeError("Dependency not injected")


def get_database():
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/lights/boards")
async def list_boards(
    dfr0971_manager=Depends(get_dfr0971_manager)
) -> Dict[str, Any]:
    """List all configured DFR0971 boards."""
    boards = dfr0971_manager.list_boards()
    return {
        "boards": boards,
        "count": len(boards)
    }


@router.post("/api/lights/{location}/{cluster}/{device_name}/intensity")
async def set_intensity(
    location: str,
    cluster: str,
    device_name: str,
    control: IntensityControl,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
    relay_manager=Depends(get_relay_manager),
    interlock_manager=Depends(get_interlock_manager),
    database=Depends(get_database)
) -> Dict[str, Any]:
    """
    Set dimming intensity for a light device.
    
    The device must be configured in automation_config.yaml with:
    - dimming_enabled: true
    - dimming_type: "dfr0971"
    - dimming_board_id: <board_id>
    - dimming_channel: <0 or 1>
    """
    # Validate intensity
    if control.intensity < 0 or control.intensity > 100:
        raise HTTPException(
            status_code=400,
            detail="Intensity must be between 0 and 100"
        )
    
    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
    
    if not device_info:
        raise HTTPException(
            status_code=404,
            detail=f"Device not found: {location}/{cluster}/{device_name}"
        )
    
    # Check if dimming is enabled
    if not device_info.get('dimming_enabled', False):
        raise HTTPException(
            status_code=400,
            detail=f"Dimming not enabled for device {device_name}"
        )
    
    if device_info.get('dimming_type') != 'dfr0971':
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} is not configured for DFR0971 dimming"
        )
    
    # Get board_id and channel from config
    board_id = device_info.get('dimming_board_id')
    channel = device_info.get('dimming_channel')
    
    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} missing dimming_board_id or dimming_channel configuration"
        )
    
    if channel not in [0, 1]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid dimming_channel: {channel} (must be 0 or 1)"
        )
    
    # Check interlock before setting intensity
    if relay_manager and interlock_manager:
        # Get current device states for interlock check
        device_states = relay_manager.get_all_states()
        
        # Check interlock with requested intensity
        can_set_intensity, reason = interlock_manager.check_interlock(
            location, cluster, device_name, device_states, requested_load=control.intensity
        )
        
        if not can_set_intensity:
            raise HTTPException(
                status_code=409,  # Conflict
                detail=reason or "Interlock blocked: Cannot set intensity due to interlock constraint"
        )
    
    # Set intensity
    success = dfr0971_manager.set_intensity(board_id, channel, control.intensity)
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set intensity for board {board_id}, channel {channel}"
        )
    
    # Get current voltage
    voltage = dfr0971_manager.get_voltage(board_id, channel)
    
    # Store in Redis for persistence across service restarts
    if database and database._automation_redis:
        database._automation_redis.write_light_intensity(
            location, cluster, device_name,
            control.intensity, voltage,
            board_id, channel
        )
    
    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": control.intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel
    }


@router.get("/api/lights/{location}/{cluster}/{device_name}/status")
async def get_light_status(
    location: str,
    cluster: str,
    device_name: str,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
    database=Depends(get_database)
) -> Dict[str, Any]:
    """Get current light status (intensity, voltage, board info)."""
    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
    
    if not device_info:
        raise HTTPException(
            status_code=404,
            detail=f"Device not found: {location}/{cluster}/{device_name}"
        )
    
    # Check if dimming is enabled
    if not device_info.get('dimming_enabled', False):
        raise HTTPException(
            status_code=400,
            detail=f"Dimming not enabled for device {device_name}"
        )
    
    # Get board_id and channel
    board_id = device_info.get('dimming_board_id')
    channel = device_info.get('dimming_channel')
    
    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} missing dimming configuration"
        )
    
    # Try to read from Redis first (persistent storage)
    # If not in Redis, read from driver (which tracks current state)
    intensity = None
    voltage = None
    
    if database and database._automation_redis:
        redis_data = database._automation_redis.read_light_intensity(location, cluster, device_name)
        if redis_data:
            intensity = redis_data.get('intensity')
            voltage = redis_data.get('voltage')
    
    # Fallback to driver state if not in Redis
    if intensity is None or voltage is None:
        intensity = dfr0971_manager.get_intensity(board_id, channel)
        voltage = dfr0971_manager.get_voltage(board_id, channel)
    
    if intensity is None or voltage is None:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read status for board {board_id}, channel {channel}"
        )
    
    # Get board info
    boards = dfr0971_manager.list_boards()
    board_info = next((b for b in boards if b['board_id'] == board_id), None)
    
    return {
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel,
        "board_info": board_info
    }


@router.post("/api/lights/{location}/{cluster}/{device_name}/voltage")
async def set_voltage(
    location: str,
    cluster: str,
    device_name: str,
    control: VoltageControl,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config)
) -> Dict[str, Any]:
    """
    Set voltage directly for a light device (0-10V).
    
    This is an alternative to set_intensity for direct voltage control.
    """
    # Validate voltage
    if control.voltage < 0 or control.voltage > 10:
        raise HTTPException(
            status_code=400,
            detail="Voltage must be between 0 and 10"
        )
    
    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
    
    if not device_info:
        raise HTTPException(
            status_code=404,
            detail=f"Device not found: {location}/{cluster}/{device_name}"
        )
    
    if not device_info.get('dimming_enabled', False):
        raise HTTPException(
            status_code=400,
            detail=f"Dimming not enabled for device {device_name}"
        )
    
    # Get board_id and channel
    board_id = device_info.get('dimming_board_id')
    channel = device_info.get('dimming_channel')
    
    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} missing dimming configuration"
        )
    
    # Set voltage
    success = dfr0971_manager.set_voltage(board_id, channel, control.voltage)
    
    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set voltage for board {board_id}, channel {channel}"
        )
    
    # Calculate intensity
    intensity = (control.voltage / 10.0) * 100.0
    
    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": intensity,
        "voltage": control.voltage,
        "board_id": board_id,
        "channel": channel
    }

