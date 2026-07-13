"""Light CRUD endpoints (create, update, delete)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from app.database import DatabaseManager
from app.models.device_registry import LightDevice, LightDeviceCreate, LightDeviceUpdate
from app.repositories.devices import DeviceRepository
from app.routes.lights import get_database, get_device_repo, router
from app.services.schedule_auto_create import create_default_intensity_for_light


@router.post("/api/lights")
async def create_light(
    body: LightDeviceCreate,
    device_repo: DeviceRepository = Depends(get_device_repo),
    database: DatabaseManager = Depends(get_database),
) -> LightDevice:
    """Create a new light device on an empty DFR slot."""
    # Conflict check: (board_id, dimming_channel) must be unoccupied
    async for loc, clu, dev_name, dev_info in device_repo.iter_all_devices_flat():
        if (
            dev_info.get("dimming_board_id") == body.board_id
            and dev_info.get("dimming_channel") == body.dimming_channel
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"DFR channel already occupied by {loc}/{clu}/{dev_name} "
                    f"(board_id={body.board_id}, channel={body.dimming_channel})"
                ),
            )

    per_room_index = body.per_room_index
    if per_room_index is None:
        room_lights = await device_repo.get_lights_by_room(body.room)
        max_index = max((light.per_room_index for light in room_lights), default=0)
        per_room_index = max_index + 1

    light = await device_repo.create_light(
        board_id=body.board_id,
        dimming_channel=body.dimming_channel,
        room=body.room,
        display_name=body.display_name,
        per_room_index=per_room_index,
    )
    if light.device_id is not None:
        await create_default_intensity_for_light(
            database=database,
            device_id=light.device_id,
            location=light.location,
            cluster=light.cluster,
        )
    return light


@router.put("/api/lights/{device_id}")
async def update_light(
    device_id: int,
    body: LightDeviceUpdate,
    device_repo: DeviceRepository = Depends(get_device_repo),
) -> LightDevice:
    """Update an existing light device."""
    existing = await device_repo.get_light_by_id(device_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

    old_name = existing.device_name
    old_location = existing.location
    old_cluster = existing.cluster

    update_fields: dict[str, Any] = {}
    if body.display_name is not None:
        update_fields["display_name"] = body.display_name
    if body.room is not None:
        update_fields["room"] = body.room
    if body.per_room_index is not None:
        update_fields["per_room_index"] = body.per_room_index
    if body.relay_channel is not None:
        update_fields["relay_channel"] = body.relay_channel
    if body.safety_level is not None:
        update_fields["safety_level"] = body.safety_level
    if "board_id" in body.model_fields_set:
        update_fields["dimming_board_id"] = body.board_id
    if "dimming_channel" in body.model_fields_set:
        update_fields["dimming_channel"] = body.dimming_channel

    updated = await device_repo.update_light(device_id, **update_fields)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update light")

    # CASCADE if device_name changed
    if updated.device_name != old_name:
        await device_repo.cascade_device_name_change(
            old_name=old_name,
            new_name=updated.device_name,
            location=old_location,
            cluster=old_cluster,
        )

    return updated


@router.delete("/api/lights/{device_id}")
async def delete_light(
    device_id: int,
    device_repo: DeviceRepository = Depends(get_device_repo),
) -> dict[str, Any]:
    """Delete a light device. Warns if relay channel is still bound."""
    existing = await device_repo.get_light_by_id(device_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

    warning = None
    if existing.relay_channel is not None:
        warning = (
            f"Light {existing.display_name} had relay channel {existing.relay_channel} bound; "
            "relay channel is now free"
        )

    deleted = await device_repo.delete_light(device_id)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete light")

    result: dict[str, Any] = {"success": True, "device_id": device_id}
    if warning:
        result["warning"] = warning
    return result
