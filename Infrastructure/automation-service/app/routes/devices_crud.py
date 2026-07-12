"""Unified device registry CRUD endpoints.

Provides POST/PUT/DELETE/GET /api/devices/registry for all device types
(light and non-light) backed by the device_registry DB table.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.models.device_registry import (
    _UI_TO_DB_DEVICE_TYPES,
    Device,
    DeviceCreate,
    DeviceUpdate,
    LightDevice,
    LightDeviceCreate,
    LightDeviceUpdate,
)
from app.repositories.devices import DeviceRepository, _room_prefix
from shared.fastapi_helpers import is_production
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


# These will be overridden by main app
def get_device_repo() -> DeviceRepository:
    """Dependency to get device repository."""
    from app.main import container

    return container.get_database().device_repo


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    from app.main import container

    return container.get_config()


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import container

    return container.get_database()


@router.get("/api/devices/registry")
async def list_registry_devices(
    device_repo: DeviceRepository = Depends(get_device_repo),
) -> list[Device | LightDevice]:
    """Return all devices (light and non-light) as a flat typed list."""
    return await device_repo.get_all_devices_flat()


@router.post("/api/devices/registry")
async def create_registry_device(
    body: dict[str, Any],
    device_repo: DeviceRepository = Depends(get_device_repo),
    config: ConfigLoader = Depends(get_config),
) -> Device | LightDevice:
    """Create a new device (light or non-light) in the registry."""
    device_type = body.get("device_type")
    if device_type is None:
        raise HTTPException(status_code=400, detail="device_type is required")

    if device_type == "light":
        # Validate room before creating the model so we can catch ValueError
        room = body.get("room")
        if room is None:
            raise HTTPException(status_code=400, detail="room is required")
        try:
            _room_prefix(room)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # DFR channel conflict check (409, matching lights.py pattern)
        board_id = body.get("board_id")
        dimming_channel = body.get("dimming_channel")
        if board_id is None or dimming_channel is None:
            raise HTTPException(
                status_code=400, detail="board_id and dimming_channel are required for lights"
            )

        hierarchy = await device_repo.get_all_as_hierarchy()
        for loc, clusters in hierarchy.items():
            for clu, devices in clusters.items():
                for dev_name, dev_info in devices.items():
                    if (
                        dev_info.get("dimming_board_id") == board_id
                        and dev_info.get("dimming_channel") == dimming_channel
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"DFR channel already occupied by {loc}/{clu}/{dev_name} "
                                f"(board_id={board_id}, channel={dimming_channel})"
                            ),
                        )

        # Build LightDeviceCreate without the device_type field
        light_body = {k: v for k, v in body.items() if k != "device_type"}
        try:
            light_create = LightDeviceCreate(**light_body)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid light device data: {exc}"
            ) from exc

        per_room_index = light_create.per_room_index
        if per_room_index is None:
            room_lights = await device_repo.get_lights_by_room(light_create.room)
            max_index = max((light.per_room_index for light in room_lights), default=0)
            per_room_index = max_index + 1

        created = await device_repo.create_light(
            board_id=light_create.board_id,
            dimming_channel=light_create.dimming_channel,
            room=light_create.room,
            display_name=light_create.display_name,
            per_room_index=per_room_index,
        )
    else:
        # Non-light device
        canonical_type = _UI_TO_DB_DEVICE_TYPES.get(device_type, device_type)
        body["device_type"] = canonical_type

        room = body.get("room")
        if room is None:
            raise HTTPException(status_code=400, detail="room is required")
        try:
            _room_prefix(room)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Relay channel conflict check (global)
        channel = body.get("channel")
        if channel is not None:
            hierarchy = await device_repo.get_all_as_hierarchy()
            for loc, clusters in hierarchy.items():
                for clu, devices in clusters.items():
                    for dev_name, dev_info in devices.items():
                        if dev_info.get("channel") == channel:
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"Relay channel {channel} already occupied by "
                                    f"{loc}/{clu}/{dev_name}"
                                ),
                            )

        try:
            device_create = DeviceCreate(**body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid device data: {exc}") from exc

        created = await device_repo.create_device(device_create)

    config.invalidate_device_cache()
    return created


@router.put("/api/devices/registry/{device_id}")
async def update_registry_device(
    device_id: int,
    body: dict[str, Any],
    device_repo: DeviceRepository = Depends(get_device_repo),
    config: ConfigLoader = Depends(get_config),
) -> Device | LightDevice:
    """Update an existing device (light or non-light)."""
    current_type = await device_repo.get_device_type_by_id(device_id)
    if current_type is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    if current_type == "light":
        existing = await device_repo.get_light_by_id(device_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

        old_name = existing.device_name
        old_location = existing.location
        old_cluster = existing.cluster

        # Filter to LightDeviceUpdate fields only
        light_fields = {k: v for k, v in body.items() if k in LightDeviceUpdate.model_fields}
        try:
            light_update = LightDeviceUpdate(**light_fields)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid light update data: {exc}"
            ) from exc

        update_fields: dict[str, Any] = {}
        if light_update.display_name is not None:
            update_fields["display_name"] = light_update.display_name
        if light_update.room is not None:
            update_fields["room"] = light_update.room
        if light_update.per_room_index is not None:
            update_fields["per_room_index"] = light_update.per_room_index
        if light_update.relay_channel is not None:
            update_fields["relay_channel"] = light_update.relay_channel
        if light_update.safety_level is not None:
            update_fields["safety_level"] = light_update.safety_level
        if "board_id" in light_fields:
            update_fields["dimming_board_id"] = light_fields["board_id"]
        if "dimming_channel" in light_fields:
            update_fields["dimming_channel"] = light_fields["dimming_channel"]

        # Relay channel conflict check for lights on update
        if "relay_channel" in update_fields and update_fields["relay_channel"] is not None:
            hierarchy = await device_repo.get_all_as_hierarchy()
            for loc, clusters in hierarchy.items():
                for clu, devices in clusters.items():
                    for dev_name, dev_info in devices.items():
                        if (
                            dev_info.get("channel") == update_fields["relay_channel"]
                            and dev_info.get("device_id") != device_id
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"Relay channel {update_fields['relay_channel']} already occupied by "
                                    f"{loc}/{clu}/{dev_name}"
                                ),
                            )

        # DFR channel conflict check for lights on update
        if "dimming_board_id" in update_fields and update_fields["dimming_board_id"] is not None:
            hierarchy = await device_repo.get_all_as_hierarchy()
            for loc, clusters in hierarchy.items():
                for clu, devices in clusters.items():
                    for dev_name, dev_info in devices.items():
                        if (
                            dev_info.get("dimming_board_id") == update_fields["dimming_board_id"]
                            and dev_info.get("dimming_channel")
                            == update_fields.get("dimming_channel")
                            and dev_info.get("device_id") != device_id
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"DFR channel already occupied by {loc}/{clu}/{dev_name} "
                                    f"(board_id={update_fields['dimming_board_id']}, "
                                    f"channel={update_fields.get('dimming_channel')})"
                                ),
                            )

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

        config.invalidate_device_cache()
        return updated
    else:
        # Non-light device
        device_fields = {k: v for k, v in body.items() if k in DeviceUpdate.model_fields}
        try:
            device_update = DeviceUpdate(**device_fields)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid device update data: {exc}"
            ) from exc

        # Relay channel conflict check for non-lights on update
        if device_update.channel is not None:
            hierarchy = await device_repo.get_all_as_hierarchy()
            for loc, clusters in hierarchy.items():
                for clu, devices in clusters.items():
                    for dev_name, dev_info in devices.items():
                        if (
                            dev_info.get("channel") == device_update.channel
                            and dev_info.get("device_id") != device_id
                        ):
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"Relay channel {device_update.channel} already occupied by "
                                    f"{loc}/{clu}/{dev_name}"
                                ),
                            )

        updated = await device_repo.update_device(device_id, device_update)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        config.invalidate_device_cache()
        return updated


@router.delete("/api/devices/registry/{device_id}")
async def delete_registry_device(
    device_id: int,
    request: Request,
    device_repo: DeviceRepository = Depends(get_device_repo),
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Delete a device from the registry.

    For lights: cascades schedule and effective_setpoint cleanup.
    """
    if is_production() and request.headers.get("X-Confirm-Destructive") != "true":
        raise HTTPException(
            status_code=403,
            detail="Destructive operation on device_registry requires X-Confirm-Destructive: true header in production.",
        )

    current_type = await device_repo.get_device_type_by_id(device_id)
    if current_type is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    if current_type == "light":
        existing = await device_repo.get_light_by_id(device_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

        warning = None
        if existing.relay_channel is not None:
            warning = (
                f"Light {existing.display_name} had relay channel {existing.relay_channel} bound; "
                "relay channel is now free"
            )

        # Cascade: delete schedules referencing this light
        deleted_schedules = await database.schedule_repo.delete_schedules_by_device_name(
            existing.location, existing.cluster, existing.device_name
        )

        # Cascade: delete effective_setpoints referencing this light
        try:
            pool = await database._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """DELETE FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    existing.location,
                    existing.cluster,
                    existing.device_name,
                )
        except Exception as e:
            logger.error(
                f"Failed to cascade delete effective_setpoints for {existing.device_name}: {e}"
            )

        deleted = await device_repo.delete_light(device_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete light")

        result: dict[str, Any] = {
            "success": True,
            "device_id": device_id,
            "deleted_schedules": deleted_schedules,
        }
        if warning:
            result["warning"] = warning
        config.invalidate_device_cache()
        return result
    else:
        deleted = await device_repo.delete_device(device_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        config.invalidate_device_cache()
        return {"success": True, "device_id": device_id}
