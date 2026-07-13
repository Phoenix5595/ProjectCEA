"""Light control endpoints (direct hardware intensity/voltage)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, HTTPException

from app.config import ConfigLoader
from app.hardware.dfr0971 import DFR0971Manager
from app.routes.lights import (
    get_config,
    get_dfr0971_manager,
    get_interlock_manager,
    get_relay_manager,
    router,
)
from app.schemas.lights import IntensityControl, VoltageControl
from shared.infra_logging import get_logger

logger = get_logger(__name__)


@router.post("/api/lights/{location}/{cluster}/{device_name}/intensity")
async def set_intensity(
    location: str,
    cluster: str,
    device_name: str,
    control: IntensityControl,
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
    config: ConfigLoader = Depends(get_config),
    relay_manager: Any = Depends(get_relay_manager),
    interlock_manager: Any = Depends(get_interlock_manager),
) -> dict[str, Any]:
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
        raise HTTPException(status_code=400, detail="Intensity must be between 0 and 100")

    # Get device configuration
    devices = await config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    # Check if dimming is enabled
    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    if device_info.get("dimming_type") != "dfr0971":
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} is not configured for DFR0971 dimming"
        )

    # Get board_id and channel from config
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} missing dimming_board_id or dimming_channel configuration",
        )

    if channel not in [0, 1]:
        raise HTTPException(
            status_code=400, detail=f"Invalid dimming_channel: {channel} (must be 0 or 1)"
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
                detail=reason
                or "Interlock blocked: Cannot set intensity due to interlock constraint",
            )

    # Sync relay state with dimmer (same order as device_controller._control_dimmable_light)
    if relay_manager:
        if control.intensity > 0:
            relay_manager.set_device_state(location, cluster, device_name, 1)

    # Set intensity (dimmer) - dfr0971 driver does ~50-100ms of I2C bus sleeps,
    # so offload to a worker thread to keep the event loop responsive.
    success = await asyncio.to_thread(
        dfr0971_manager.set_intensity, board_id, channel, control.intensity
    )

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set intensity for board {board_id}, channel {channel}",
        )

    if relay_manager and control.intensity == 0:
        relay_manager.set_device_state(location, cluster, device_name, 0)

    # Get current voltage
    voltage = dfr0971_manager.get_voltage(board_id, channel)

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": control.intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel,
    }


@router.post("/api/lights/{location}/{cluster}/{device_name}/voltage")
async def set_voltage(
    location: str,
    cluster: str,
    device_name: str,
    control: VoltageControl,
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """
    Set voltage directly for a light device (0-10V).

    This is an alternative to set_intensity for direct voltage control.
    """
    # Validate voltage
    if control.voltage < 0 or control.voltage > 10:
        raise HTTPException(status_code=400, detail="Voltage must be between 0 and 10")

    # Get device configuration
    devices = await config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    # Get board_id and channel
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} missing dimming configuration"
        )

    # Set voltage - offloaded to worker thread (driver does blocking I2C sleeps).
    success = await asyncio.to_thread(
        dfr0971_manager.set_voltage, board_id, channel, control.voltage
    )

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to set voltage for board {board_id}, channel {channel}"
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
        "channel": channel,
    }
