"""Light test endpoint (DFR intensity sweep)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, HTTPException

from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager
from app.hardware.i2c_lock import acquire_i2c_bus_1
from app.repositories.devices import DeviceRepository
from app.routes.lights import (
    get_database,
    get_device_repo,
    get_dfr0971_manager,
    get_relay_manager,
    router,
)


@router.post("/api/lights/{device_id}/test")
async def test_light(
    device_id: int,
    device_repo: DeviceRepository = Depends(get_device_repo),
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
    database: DatabaseManager = Depends(get_database),
    relay_manager: Any = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Run a 5-second DFR intensity sweep on a light device.

    Sequence: 100% -> 10% -> 100% over ~5 seconds.
    Prior intensity and mode are restored even if an exception occurs.
    """
    light = await device_repo.get_light_by_id(device_id)
    if light is None:
        raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

    if light.board_id is None or light.dimming_channel is None:
        raise HTTPException(status_code=400, detail="Light has no DFR configuration")

    # Failsafe check
    if database and database._automation_redis:
        failsafe = database._automation_redis.read_failsafe(light.location, light.cluster)
        if failsafe is not None:
            raise HTTPException(
                status_code=423,
                detail=f"Room {light.location}/{light.cluster} is in failsafe mode",
            )

    i2c_lock = await acquire_i2c_bus_1()
    if i2c_lock.locked():
        raise HTTPException(status_code=409, detail="I2C bus 1 is busy")

    prior_intensity: float | None = None
    prior_relay_state: int | None = None
    prior_mode: str | None = None

    async with i2c_lock:
        # Read prior intensity
        prior_intensity = dfr0971_manager.get_intensity(light.board_id, light.dimming_channel)
        if prior_intensity is None:
            prior_intensity = 0.0

        # Set to manual mode if relay is bound
        if light.relay_channel is not None and relay_manager:
            prior_relay_state = relay_manager.get_device_state(
                light.location, light.cluster, light.device_name
            )
            prior_mode = relay_manager.get_device_mode(
                light.location, light.cluster, light.device_name
            )
            relay_success, relay_reason = await relay_manager.set_device_state(
                light.location, light.cluster, light.device_name, 1, "manual"
            )
            if not relay_success:
                raise HTTPException(status_code=503, detail=relay_reason or "Relay hardware error")
            if database:
                await database.device_repo.set_device_state(
                    light.location,
                    light.cluster,
                    light.device_name,
                    light.relay_channel,
                    True,
                    "manual",
                )

        try:
            # Sweep: 100% -> 10% -> 100% over ~5s
            await asyncio.to_thread(
                dfr0971_manager.set_intensity, light.board_id, light.dimming_channel, 100.0
            )
            await asyncio.sleep(1.5)

            await asyncio.to_thread(
                dfr0971_manager.set_intensity, light.board_id, light.dimming_channel, 10.0
            )
            await asyncio.sleep(1.5)

            await asyncio.to_thread(
                dfr0971_manager.set_intensity, light.board_id, light.dimming_channel, 100.0
            )
            await asyncio.sleep(1.5)
        finally:
            # Restore prior intensity
            await asyncio.to_thread(
                dfr0971_manager.set_intensity,
                light.board_id,
                light.dimming_channel,
                prior_intensity,
            )

            # Restore prior relay state and mode
            if light.relay_channel is not None and relay_manager:
                restore_state = prior_relay_state if prior_relay_state is not None else 0
                relay_success, _relay_reason = await relay_manager.set_device_state(
                    light.location, light.cluster, light.device_name, restore_state
                )
                if database and prior_mode is not None and relay_success:
                    await database.device_repo.set_device_state(
                        light.location,
                        light.cluster,
                        light.device_name,
                        light.relay_channel,
                        bool(restore_state),
                        prior_mode,
                    )

    return {
        "success": True,
        "device_id": device_id,
        "device_name": light.device_name,
        "prior_intensity": prior_intensity,
        "message": "DFR test sweep completed (100% -> 10% -> 100%)",
    }
