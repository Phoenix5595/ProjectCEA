from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes.lights import light_test


class _Sink:
    def __init__(self, completed: list[str] | None = None) -> None:
        self._completed = completed
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        if self._completed is not None:
            assert self._completed == ["restored"]
        self.events.append(event)


class _LightRepository:
    def __init__(self, light: SimpleNamespace | None, completed: list[str]) -> None:
        self.light = light
        self.completed = completed
        self.states: list[tuple[bool, str]] = []

    async def get_light_by_id(self, _device_id: int) -> SimpleNamespace | None:
        return self.light

    async def set_device_state(self, *_args: object) -> None:
        self.states.append((_args[-2], _args[-1]))
        if len(self.states) == 2:
            self.completed.append("restored")


class _Lock:
    def locked(self) -> bool:
        return False

    async def __aenter__(self) -> _Lock:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_light_test_emits_one_action_only_after_restoring_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a relay-bound light whose test records manual entry and restored state.
    completed: list[str] = []
    light = SimpleNamespace(
        board_id=0,
        dimming_channel=1,
        relay_channel=2,
        location="Veg Room",
        cluster="main",
        device_name="light_1",
    )
    repository = _LightRepository(light, completed)
    monkeypatch.setattr(light_test, "acquire_i2c_bus_1", _lock)
    monkeypatch.setattr(light_test.asyncio, "sleep", _nothing)
    dfr = SimpleNamespace(get_intensity=lambda *_args: 40.0, set_intensity=lambda *_args: True)
    relay = SimpleNamespace(
        get_device_state=lambda *_args: 0,
        get_device_mode=lambda *_args: "auto",
        set_device_state=_relay_success,
    )
    sink = _Sink(completed)

    # When: the DFR sweep succeeds and its relay state is restored.
    response = await light_test.test_light(
        1,
        repository,
        dfr,
        SimpleNamespace(_automation_redis=None, device_repo=repository),
        relay,
        MutationRequestContext.create(),
        sink,
    )

    # Then: the route emits one completed action, not a duplicate relay lifecycle row.
    assert response["success"] is True
    assert len(sink.events) == 1


@pytest.mark.asyncio
async def test_light_test_validation_failure_is_event_silent() -> None:
    # Given: a registry light lacking the required DFR identity.
    sink = _Sink()
    repository = _LightRepository(SimpleNamespace(board_id=None, dimming_channel=None), [])

    # When: the test endpoint validates its hardware configuration.
    with pytest.raises(HTTPException) as error:
        await light_test.test_light(
            1,
            repository,
            SimpleNamespace(),
            SimpleNamespace(_automation_redis=None, device_repo=repository),
            None,
            MutationRequestContext.create(),
            sink,
        )

    # Then: no action-completed event is emitted for an invalid request.
    assert error.value.status_code == 400
    assert sink.events == []


async def _nothing(*_args: object, **_kwargs: object) -> None:
    return None


async def _lock() -> _Lock:
    return _Lock()


async def _relay_success(*_args: object) -> tuple[bool, None]:
    return True, None
