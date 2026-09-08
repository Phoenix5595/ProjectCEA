"""HTTP boundary for canonical device-registry mutations."""

from __future__ import annotations

from typing import Annotated, Any, assert_never

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.events.mutation_context import (
    MutationOperation,
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
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
@emits_operational_mutation
async def create_registry_device(
    body: Annotated[RegistryDeviceCreate, Body(discriminator="device_type")],
    confirmed_relay_steal: bool = False,
    service: DeviceRegistryService = Depends(get_device_registry_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
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
    _emit_registry_mutation(sink, context, "create", mutation)
    return _mutation_response(mutation)


@router.put("/api/devices/registry/{device_id}")
@emits_operational_mutation
async def update_registry_device(
    device_id: int,
    body: RegistryDeviceUpdate,
    confirmed_relay_steal: bool = False,
    service: DeviceRegistryService = Depends(get_device_registry_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Update any registry device through the single mutation service."""
    previous = _find_registry_device(await service.list_devices(), device_id)
    try:
        mutation = await service.update_registry_device(
            device_id,
            body.model_dump(exclude_unset=True),
            confirmed_relay_steal=confirmed_relay_steal,
        )
    except (RegistryConflictError, RegistryNotFoundError, SafeOutputError, ValueError) as error:
        raise _registry_http_error(error) from error
    _emit_registry_mutation(sink, context, "update", mutation, previous)
    return _mutation_response(mutation)


@router.patch("/api/devices/registry/{device_id}/relay")
@emits_operational_mutation
async def unbind_registry_relay(
    device_id: int,
    service: DeviceRegistryService = Depends(get_device_registry_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Explicitly unbind a relay only after its old output has been turned off."""
    previous = _find_registry_device(await service.list_devices(), device_id)
    try:
        mutation = await service.unbind_relay(device_id)
    except (RegistryNotFoundError, SafeOutputError) as error:
        raise _registry_http_error(error) from error
    _emit_registry_mutation(sink, context, "update", mutation, previous)
    return _mutation_response(mutation)


@router.delete("/api/devices/registry/{device_id}")
@emits_operational_mutation
async def delete_registry_device(
    device_id: int,
    request: Request,
    service: DeviceRegistryService = Depends(get_device_registry_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Safely delete one registry device after the production confirmation guard."""
    if is_production() and request.headers.get("X-Confirm-Destructive") != "true":
        raise HTTPException(
            status_code=403,
            detail="Destructive operation on device_registry requires X-Confirm-Destructive: true header in production.",
        )
    previous = _find_registry_device(await service.list_devices(), device_id)
    try:
        mutation = await service.delete_registry_device(device_id)
    except (RegistryNotFoundError, SafeOutputError) as error:
        raise _registry_http_error(error) from error
    _emit_registry_mutation(sink, context, "delete", mutation, previous)
    return {"success": True, "device_id": device_id, **_mutation_response(mutation)}


def _mutation_response(mutation: DeviceMutation) -> dict[str, Any]:
    """Serialize the service result while preserving confirmed-steal details."""
    response = mutation.device.model_dump() if mutation.device is not None else {}
    response["displaced_device_id"] = mutation.displaced_device_id
    return response


def _find_registry_device(
    devices: list[Device | LightDevice], device_id: int
) -> Device | LightDevice | None:
    return next((device for device in devices if device.device_id == device_id), None)


def _emit_registry_mutation(
    sink: OperationalEventSink,
    context: MutationRequestContext,
    operation: MutationOperation,
    mutation: DeviceMutation,
    previous: Device | LightDevice | None = None,
) -> None:
    device = mutation.device
    entity_device = device or previous
    if entity_device is None:
        return
    before = _device_event_values(previous)
    after = _device_event_values(device)
    if mutation.displaced_device_id is not None:
        after["displaced_device_id"] = mutation.displaced_device_id
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation=operation,
            entity=EntityContext(
                entity_type="device",
                entity_id=str(entity_device.device_id),
                location=entity_device.location,
                cluster=entity_device.cluster,
            ),
            changes=safe_allowlisted_diff(
                before=before,
                after=after,
                allowed_fields=frozenset(after | before),
            ),
        ),
        context,
    )


def _device_event_values(device: Device | LightDevice | None) -> dict[str, str | int | None]:
    if device is None:
        return {}
    values: dict[str, str | int | None] = {
        "device_id": device.device_id,
        "device_type": device.device_type,
        "display_name": device.display_name,
        "device_name": device.device_name,
    }
    if isinstance(device, LightDevice):
        values.update(
            {
                "relay_channel": device.relay_channel,
                "board_id": device.board_id,
                "dimming_channel": device.dimming_channel,
            }
        )
    else:
        values["channel"] = device.channel
    return values


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
