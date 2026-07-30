"""HTTP boundary for canonical device-registry mutations."""

from __future__ import annotations

from typing import Annotated, Any, assert_never

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.models.device_registry import (
    Device,
    DeviceCreate,
    LightDevice,
    LightDeviceCreate,
    RegistryDeviceCreate,
    RegistryDeviceUpdate,
)
from app.services.device_registry_service import (
    DeviceMutation,
    DeviceRegistryService,
    RegistryConflictError,
    RegistryNotFoundError,
    SafeOutputError,
)
from shared.fastapi_helpers import is_production

router = APIRouter()


def get_device_registry_service() -> DeviceRegistryService:
    """Resolve the canonical device-registry mutation service."""
    from app.main import container

    return container.get_device_registry_service()


@router.get("/api/devices/registry")
async def list_registry_devices(
    service: DeviceRegistryService = Depends(get_device_registry_service),
) -> list[Device | LightDevice]:
    """Return typed registry devices through the service read boundary."""
    return await service.list_devices()


@router.post("/api/devices/registry")
async def create_registry_device(
    body: Annotated[RegistryDeviceCreate, Body(discriminator="device_type")],
    confirmed_relay_steal: bool = False,
    service: DeviceRegistryService = Depends(get_device_registry_service),
) -> dict[str, Any]:
    """Create a device with atomic assignment conflict handling."""
    try:
        match body:
            case LightDeviceCreate() as light_create:
                mutation = await service.create_light(
                    light_create, confirmed_relay_steal=confirmed_relay_steal
                )
            case DeviceCreate() as device_create:
                mutation = await service.create_device(
                    device_create, confirmed_relay_steal=confirmed_relay_steal
                )
            case unreachable:
                assert_never(unreachable)
    except (RegistryConflictError, RegistryNotFoundError, SafeOutputError, ValueError) as error:
        raise _registry_http_error(error) from error
    return _mutation_response(mutation)


@router.put("/api/devices/registry/{device_id}")
async def update_registry_device(
    device_id: int,
    body: RegistryDeviceUpdate,
    confirmed_relay_steal: bool = False,
    service: DeviceRegistryService = Depends(get_device_registry_service),
) -> dict[str, Any]:
    """Update any registry device through the single mutation service."""
    try:
        mutation = await service.update_registry_device(
            device_id,
            body.model_dump(exclude_unset=True),
            confirmed_relay_steal=confirmed_relay_steal,
        )
    except (RegistryConflictError, RegistryNotFoundError, SafeOutputError, ValueError) as error:
        raise _registry_http_error(error) from error
    return _mutation_response(mutation)


@router.patch("/api/devices/registry/{device_id}/relay")
async def unbind_registry_relay(
    device_id: int,
    service: DeviceRegistryService = Depends(get_device_registry_service),
) -> dict[str, Any]:
    """Explicitly unbind a relay only after its old output has been turned off."""
    try:
        mutation = await service.unbind_relay(device_id)
    except (RegistryNotFoundError, SafeOutputError) as error:
        raise _registry_http_error(error) from error
    return _mutation_response(mutation)


@router.delete("/api/devices/registry/{device_id}")
async def delete_registry_device(
    device_id: int,
    request: Request,
    service: DeviceRegistryService = Depends(get_device_registry_service),
) -> dict[str, Any]:
    """Safely delete one registry device after the production confirmation guard."""
    if is_production() and request.headers.get("X-Confirm-Destructive") != "true":
        raise HTTPException(
            status_code=403,
            detail="Destructive operation on device_registry requires X-Confirm-Destructive: true header in production.",
        )
    try:
        mutation = await service.delete_registry_device(device_id)
    except (RegistryNotFoundError, SafeOutputError) as error:
        raise _registry_http_error(error) from error
    return {"success": True, "device_id": device_id, **_mutation_response(mutation)}


def _mutation_response(mutation: DeviceMutation) -> dict[str, Any]:
    """Serialize the service result while preserving confirmed-steal details."""
    response = mutation.device.model_dump() if mutation.device is not None else {}
    response["displaced_device_id"] = mutation.displaced_device_id
    return response


def _registry_http_error(
    error: RegistryConflictError | RegistryNotFoundError | SafeOutputError | ValueError,
) -> HTTPException:
    """Translate domain outcomes into the API contract without mutation logic."""
    match error:
        case RegistryConflictError(assignment=assignment, owner=owner):
            owner_detail = {
                "owner_device_id": owner["device_id"],
                "owner_device_name": owner["device_name"],
                "owner_display_name": owner["display_name"],
            }
            if assignment != "relay":
                return HTTPException(
                    status_code=409,
                    detail={"assignment": assignment, **owner_detail},
                )
            return HTTPException(
                status_code=409,
                detail={
                    "assignment": assignment,
                    "displaced_device_id": owner["device_id"],
                    "displaced_device_name": owner["device_name"],
                    "displaced_display_name": owner["display_name"],
                },
            )
        case RegistryNotFoundError(device_id=device_id):
            return HTTPException(status_code=404, detail=f"Device {device_id} not found")
        case SafeOutputError(output=output):
            return HTTPException(status_code=503, detail=f"Unable to make {output} safe")
        case ValueError():
            return HTTPException(status_code=400, detail=str(error))
        case unreachable:
            assert_never(unreachable)
