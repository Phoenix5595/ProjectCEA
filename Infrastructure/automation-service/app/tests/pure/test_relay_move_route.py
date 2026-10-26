"""The relay-move route re-binds one device and reports both sides of a swap."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.mutation_context import mutation_context_middleware
from app.events.mutation_dependencies import get_mutation_event_sink
from app.events.operational_models import OperationalEvent
from app.models.device_registry import Device
from app.routes import devices_crud
from app.services.device_registry_service import RelayMove, SafeOutputError


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _RegistryService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.moves: list[tuple[int, int]] = []
        self.devices: list[Any] = [
            Device(
                device_id=1,
                device_type="heating",
                channel=2,
                display_name="Heat",
                device_name="heating_v_1",
                location="Veg Room",
            ),
            Device(
                device_id=2,
                device_type="exhaust",
                channel=5,
                display_name="Exhaust",
                device_name="exhaust_v_1",
                location="Veg Room",
            ),
        ]

    async def list_devices(self) -> list[Any]:
        return self.devices

    async def move_relay(self, device_id: int, channel: int) -> RelayMove:
        if self.error is not None:
            raise self.error
        self.moves.append((device_id, channel))
        mover, owner = self.devices
        return RelayMove(
            device=mover.model_copy(update={"channel": channel}),
            swapped_device=owner.model_copy(update={"channel": mover.channel}),
        )


def _client(service: Any, sink: _RecordingSink) -> AsyncClient:
    app = FastAPI()
    app.middleware("http")(mutation_context_middleware)
    app.include_router(devices_crud.router)
    app.dependency_overrides[devices_crud.get_device_registry_service] = lambda: service
    app.dependency_overrides[get_mutation_event_sink] = lambda: sink
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_relay_move_swap_returns_both_owners_and_emits_two_events() -> None:
    # Given: an occupied target channel owned by a second device.
    sink = _RecordingSink()
    service = _RegistryService()

    # When: the mover is dropped onto the occupied channel.
    async with _client(service, sink) as client:
        response = await client.put("/api/devices/registry/1/relay", json={"channel": 5})

    # Then: both owners are reported and both channel changes are visible.
    assert response.status_code == 200
    assert response.json() == {
        "device_id": 1,
        "channel": 5,
        "swapped_device_id": 2,
        "swapped_channel": 2,
    }
    assert service.moves == [(1, 5)]
    assert [event.event_type for event in sink.events] == ["mutation.updated", "mutation.updated"]
    assert [event.entity.entity_id for event in sink.events] == ["1", "2"]
    assert [[change.key for change in event.payload.changes] for event in sink.events] == [
        ["channel"],
        ["channel"],
    ]


@pytest.mark.asyncio
async def test_relay_move_rejects_a_channel_above_the_bus_width() -> None:
    # Given: a request channel outside the 0-15 MCP23017 range.
    sink = _RecordingSink()
    service = _RegistryService()

    # When: the body is validated before any binding can change.
    async with _client(service, sink) as client:
        response = await client.put("/api/devices/registry/1/relay", json={"channel": 16})

    # Then: the request is rejected and nothing reaches the service.
    assert response.status_code == 422
    assert service.moves == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_relay_move_reports_a_channel_that_cannot_confirm_off() -> None:
    # Given: a move whose target channel never confirms OFF.
    sink = _RecordingSink()
    service = _RegistryService(error=SafeOutputError("relay channel 5"))

    # When: the route applies the move.
    async with _client(service, sink) as client:
        response = await client.put("/api/devices/registry/1/relay", json={"channel": 5})

    # Then: the caller sees the safe-output failure and no mutation is reported.
    assert response.status_code == 503
    assert response.json() == {"detail": "Unable to make relay channel 5 safe"}
    assert sink.events == []
