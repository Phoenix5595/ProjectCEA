from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.models.device_registry import Device, DeviceCreate
from app.routes.devices_crud import create_registry_device
from app.routes.hardware import RelayChannelControlRequest, set_relay_channel_state
from app.services.device_registry_service import DeviceMutation, RegistryConflictError


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _RelayManager:
    async def set_channel_state(self, _channel: int, _state: int) -> bool:
        return True


class _DeviceCommandAuthority:
    def is_assigned_channel(self, _channel: int) -> bool:
        return False


class _Redis:
    def __init__(self, prior_override: bytes | None) -> None:
        self.prior_override = prior_override
        self.writes: list[tuple[str, object]] = []

    def get(self, _key: str) -> bytes | None:
        return self.prior_override

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.writes.append((key, value))

    def delete(self, key: str) -> None:
        self.writes.append((key, None))


class _ConflictingRegistryService:
    async def create_device(self, *_args: object, **_kwargs: object) -> None:
        raise RegistryConflictError(
            assignment="relay",
            owner={"device_id": 8, "device_name": "heater_2", "display_name": "Heater"},
        )


class _RegistryService:
    def __init__(self, mutation: DeviceMutation) -> None:
        self.mutation = mutation

    async def create_device(self, *_args: object, **_kwargs: object) -> DeviceMutation:
        return self.mutation


@pytest.mark.asyncio
async def test_registry_relay_steal_emits_one_correlated_mutation() -> None:
    # Given: a committed registry create that displaced the prior relay owner.
    sink = _RecordingSink()
    context = MutationRequestContext(UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"))
    service = _RegistryService(
        DeviceMutation(
            device=Device(
                device_id=9,
                device_type="heating",
                channel=5,
                display_name="Heater",
                device_name="heater_3",
                location="Veg Room",
            ),
            displaced_device_id=8,
        )
    )

    # When: the create route returns after the registry transaction commits.
    response = await create_registry_device(
        DeviceCreate(device_type="heater", room="Veg Room", display_name="Heater"),
        True,
        service,
        context,
        sink,
    )

    # Then: the steal is represented by exactly one correlated visible event.
    assert response["displaced_device_id"] == 8
    assert len(sink.events) == 1
    assert sink.events[0].correlation_id == context.correlation_id
    assert {change.key for change in sink.events[0].payload.changes} == {
        "channel",
        "device_id",
        "device_name",
        "device_type",
        "display_name",
        "displaced_device_id",
    }


@pytest.mark.asyncio
async def test_registry_conflict_emits_nothing_before_a_committed_mutation() -> None:
    # Given: a registry create whose authoritative transaction rejects a relay conflict.
    sink = _RecordingSink()

    # When: the route translates the conflict response.
    with pytest.raises(HTTPException) as error:
        await create_registry_device(
            DeviceCreate(device_type="heater", room="Veg Room", display_name="Heater"),
            False,
            _ConflictingRegistryService(),
            MutationRequestContext.create(),
            sink,
        )

    # Then: the conflict cannot reach the mutation event sink.
    assert error.value.status_code == 409
    assert sink.events == []


@pytest.mark.asyncio
async def test_raw_relay_override_emits_only_when_redis_state_changes() -> None:
    # Given: a raw OFF request with no persisted override to remove.
    sink = _RecordingSink()
    redis = _Redis(None)
    automation_redis = SimpleNamespace(redis_client=redis)

    # When: the route applies the idempotent raw OFF operation.
    response = await set_relay_channel_state(
        3,
        RelayChannelControlRequest(state=0),
        _RelayManager(),
        automation_redis,
        _DeviceCommandAuthority(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: Redis receives its delete but no false mutation reaches the sink.
    assert response["ok"] is True
    assert redis.writes == [("cea:relay:manual_override:3", None)]
    assert sink.events == []


@pytest.mark.asyncio
async def test_raw_relay_override_emits_after_redis_persistence() -> None:
    # Given: an OFF override whose persisted state will become a timed ON override.
    sink = _RecordingSink()
    redis = _Redis(b'{"expires_at":"2026-09-02T00:00:00+00:00","state":0}')
    automation_redis = SimpleNamespace(redis_client=redis)

    # When: the route succeeds in both hardware control and Redis persistence.
    await set_relay_channel_state(
        3,
        RelayChannelControlRequest(state=1, duration_seconds=60),
        _RelayManager(),
        automation_redis,
        _DeviceCommandAuthority(),
        MutationRequestContext.create(),
        sink,
    )

    # Then: the event is emitted only after the persisted override has changed.
    assert len(redis.writes) == 1
    assert len(sink.events) == 1
    assert sink.events[0].entity.entity_id == "3"
    assert sink.events[0].payload.changes[0].key == "expires_at"
    assert sink.events[0].payload.changes[1].key == "state"
