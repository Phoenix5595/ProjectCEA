"""Unified device registry CRUD endpoints.

Provides POST/PUT/DELETE/GET /api/devices/registry for all device types
(light and non-light) backed by the device_registry DB table.
"""

from __future__ import annotations

from typing import Annotated, Any, assert_never

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.models.device_registry import (
    Device,
    DeviceCreate,
    DeviceUpdate,
    LightDevice,
    LightDeviceCreate,
    LightDeviceUpdate,
    RegistryDeviceCreate,
)
from app.repositories.devices import DeviceRepository, _find_displaced_device
from app.services.schedule_auto_create import create_default_intensity_for_light
from shared.cluster_topology import _room_prefix
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
    body: Annotated[RegistryDeviceCreate, Body(discriminator="device_type")],
    device_repo: DeviceRepository = Depends(get_device_repo),
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> Device | LightDevice:
    """Create a new device (light or non-light) in the registry."""
    match body:
        case LightDeviceCreate() as light_create:
            # Validate room before creating the model so we can catch ValueError
            try:
                _room_prefix(light_create.room)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # DFR channel conflict check (409, matching lights.py pattern)
            async for loc, clu, dev_name, dev_info in device_repo.iter_all_devices_flat():
                if (
                    dev_info.get("dimming_board_id") == light_create.board_id
                    and dev_info.get("dimming_channel") == light_create.dimming_channel
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"DFR channel already occupied by {loc}/{clu}/{dev_name} "
                            f"(board_id={light_create.board_id}, channel={light_create.dimming_channel})"
                        ),
                    )

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
                relay_channel=light_create.relay_channel,
            )
            if created.device_id is not None:
                await create_default_intensity_for_light(
                    database=database,
                    device_id=created.device_id,
                    location=created.location,
                    cluster=created.cluster,
                )
        case DeviceCreate() as device_create:
            # Non-light device
            try:
                _room_prefix(device_create.room)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Relay channel conflict check (global)
            if device_create.channel is not None:
                async for loc, clu, dev_name, dev_info in device_repo.iter_all_devices_flat():
                    if dev_info.get("channel") == device_create.channel:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Relay channel {device_create.channel} already occupied by {loc}/{clu}/{dev_name}"
                            ),
                        )

            created = await device_repo.create_device(device_create)
        case unreachable:
            assert_never(unreachable)

    await config.refresh_runtime_device_snapshot()
    return created


@router.put("/api/devices/registry/{device_id}")
async def update_registry_device(
    device_id: int,
    body: dict[str, Any],
    device_repo: DeviceRepository = Depends(get_device_repo),
    config: ConfigLoader = Depends(get_config),
) -> Any:
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

        try:
            light_update = LightDeviceUpdate(**body)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid light update data: {exc}"
            ) from exc

        update_fields: dict[str, Any] = {}
        provided_fields = light_update.model_fields_set
        if "display_name" in provided_fields:
            update_fields["display_name"] = light_update.display_name
        if "room" in provided_fields:
            update_fields["room"] = light_update.room
        if "per_room_index" in provided_fields:
            update_fields["per_room_index"] = light_update.per_room_index
        if "relay_channel" in provided_fields:
            update_fields["relay_channel"] = light_update.relay_channel
        if "safety_level" in provided_fields:
            update_fields["safety_level"] = light_update.safety_level
        if "board_id" in provided_fields:
            update_fields["dimming_board_id"] = light_update.board_id
        if "dimming_channel" in provided_fields:
            update_fields["dimming_channel"] = light_update.dimming_channel

        # Relay channel conflict check for lights on update
        displaced_id: int | None = None
        if "relay_channel" in update_fields and update_fields["relay_channel"] is not None:
            displaced_id = await _find_displaced_device(
                device_repo, update_fields["relay_channel"], exclude_device_id=device_id
            )
            if displaced_id is not None:
                await device_repo.clear_relay_binding_only(displaced_id)
                logger.info(
                    f"Relay steal: light {device_id} took channel {update_fields['relay_channel']} from device {displaced_id}"
                )

        # DFR channel conflict check for lights on update
        if "dimming_board_id" in update_fields and update_fields["dimming_board_id"] is not None:
            async for loc, clu, dev_name, dev_info in device_repo.iter_all_devices_flat():
                if (
                    dev_info.get("dimming_board_id") == update_fields["dimming_board_id"]
                    and dev_info.get("dimming_channel") == update_fields.get("dimming_channel")
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

        await config.refresh_runtime_device_snapshot()
        response = updated.model_dump()
        response["displaced_device_id"] = displaced_id
        return response
    else:
        # Non-light device
        try:
            device_update = DeviceUpdate(**body)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid device update data: {exc}"
            ) from exc

        # Relay channel conflict check for non-lights on update
        displaced_id: int | None = None
        if device_update.channel is not None:
            displaced_id = await _find_displaced_device(
                device_repo, device_update.channel, exclude_device_id=device_id
            )
            if displaced_id is not None:
                await device_repo.clear_relay_binding_only(displaced_id)
                logger.info(
                    f"Relay steal: device {device_id} took channel {device_update.channel} from device {displaced_id}"
                )

        updated = await device_repo.update_device(device_id, device_update)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        await config.refresh_runtime_device_snapshot()
        response = updated.model_dump()
        response["displaced_device_id"] = displaced_id
        return response


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
        }
        if warning:
            result["warning"] = warning
        await config.refresh_runtime_device_snapshot()
        return result
    else:
        deleted = await device_repo.delete_device(device_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

        await config.refresh_runtime_device_snapshot()
        return {"success": True, "device_id": device_id}
