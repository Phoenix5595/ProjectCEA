from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, Request
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.models.device_registry import Device, RegistryDeviceUpdate
from app.routes import devices_crud
from app.services.device_registry_service import (
    DeviceMutation,
    RegistryNotFoundError,
    SafeOutputError,
)


class _RecordingSink:
    def __init__(self, service: _RegistryService) -> None:
        self._service = service
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        assert self._service.committed
        self.events.append(event)


class _RegistryService:
    def __init__(self, before: Device, result: DeviceMutation | Exception) -> None:
        self.before = before
        self.result = result
        self.committed = False

    async def list_devices(self) -> list[Device]:
        return [self.before]

    async def update_registry_device(self, *_args: object, **_kwargs: object) -> DeviceMutation:
        return self._complete()

    async def unbind_relay(self, _device_id: int) -> DeviceMutation:
        return self._complete()

    async def delete_registry_device(self, _device_id: int) -> DeviceMutation:
        return self._complete()

    def _complete(self) -> DeviceMutation:
        if isinstance(self.result, Exception):
            raise self.result
        self.committed = True
        return self.result


def _device(channel: int | None = 5, display_name: str = "Heater") -> Device:
    return Device(
        device_id=9,
        device_type="heating",
        channel=channel,
        display_name=display_name,
        device_name="heater_3",
        location="Veg Room",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "result", "expected_status", "expected_events", "expected_key"),
    [
        ("update", DeviceMutation(_device(display_name="Main Heater")), None, 1, "display_name"),
        ("unbind", DeviceMutation(_device(channel=None)), None, 1, "channel"),
        ("delete", DeviceMutation(None), None, 1, "channel"),
        ("update", DeviceMutation(_device()), None, 0, None),
        ("update", ValueError("invalid registry update"), 400, 0, None),
        ("unbind", SafeOutputError(output="relay"), 503, 0, None),
        ("delete", RegistryNotFoundError(device_id=9), 404, 0, None),
    ],
)
async def test_registry_write_emits_only_after_changed_committed_result(
    operation: str,
    result: DeviceMutation | Exception,
    expected_status: int | None,
    expected_events: int,
    expected_key: str | None,
) -> None:
    # Given: an authoritative registry service with a typed before-image.
    service = _RegistryService(_device(), result)
    sink = _RecordingSink(service)
    context = MutationRequestContext(UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"))

    # When: update, relay unbind, or deletion completes through its HTTP boundary.
    try:
        match operation:
            case "update":
                await devices_crud.update_registry_device(
                    9,
                    RegistryDeviceUpdate(display_name="Main Heater"),
                    False,
                    service,
                    context,
                    sink,
                )
            case "unbind":
                await devices_crud.unbind_registry_relay(9, service, context, sink)
            case "delete":
                await devices_crud.delete_registry_device(
                    9,
                    Request({"type": "http", "headers": []}),
                    service,
                    context,
                    sink,
                )
            case _:
                raise AssertionError(f"unexpected operation: {operation}")
    except HTTPException as error:
        assert error.status_code == expected_status

    # Then: only changed commits emit a safe, correlated mutation after service completion.
    assert len(sink.events) == expected_events
    if expected_key is not None:
        assert sink.events[0].correlation_id == context.correlation_id
        assert expected_key in {change.key for change in sink.events[0].payload.changes}
