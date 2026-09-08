from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes import hardware, mode
from app.routes.lights import light_target
from app.schemas.lights import LightIntensityUpdate


class _Sink:
    def __init__(self, completed: list[str] | None = None) -> None:
        self._completed = completed
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        if self._completed is not None:
            assert self._completed == ["restored"]
        self.events.append(event)


class _ModeRedis:
    redis_enabled = True

    def __init__(self, before: str, succeeds: bool) -> None:
        self.before = before
        self.succeeds = succeeds
        self.writes: list[str] = []

    def read_mode(self, *_args: str) -> str:
        return self.before

    def write_mode(self, _location: str, _cluster: str, value: str, **_kwargs: object) -> bool:
        self.writes.append(value)
        return self.succeeds


class _State:
    def __init__(self) -> None:
        self.values: list[str] = []

    async def set_mode(self, _location: str, _cluster: str, value: str, **_kwargs: object) -> None:
        self.values.append(value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "succeeds", "expected_events", "status"),
    [("auto", True, 1, None), ("manual", True, 0, None), ("auto", False, 0, 500)],
)
async def test_mode_write_emits_only_after_changed_redis_success(
    monkeypatch: pytest.MonkeyPatch,
    before: str,
    succeeds: bool,
    expected_events: int,
    status: int | None,
) -> None:
    # Given: a Redis authoritative mode before-image and an in-process cache.
    state = _State()
    monkeypatch.setattr(mode, "get_state_manager", lambda: state)
    redis = _ModeRedis(before, succeeds)
    sink = _Sink()

    # When: the caller changes, repeats, or fails to persist a mode.
    try:
        await mode.set_mode(
            "Veg Room",
            "main",
            mode.ModeUpdate(mode="manual"),
            redis,
            MutationRequestContext.create(),
            sink,
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: cache publication and mutation visibility follow successful persistence only.
    assert len(sink.events) == expected_events
    assert state.values == (["manual"] if succeeds else [])


class _Relay:
    def __init__(self, succeeds: bool) -> None:
        self.succeeds = succeeds

    async def set_channel_state(self, _channel: int, _state: int) -> bool:
        return self.succeeds


class _Redis:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def get(self, _key: str) -> bytes:
        return b'{"expires_at":"2026-09-02T00:00:00+00:00","state":0}'

    def setex(self, *_args: object) -> None:
        self.writes.append("set")

    def delete(self, *_args: object) -> None:
        self.writes.append("delete")


@pytest.mark.asyncio
async def test_raw_relay_hardware_failure_emits_no_success_mutation() -> None:
    # Given: a raw relay request whose hardware command fails before an ON override persists.
    redis = _Redis()
    sink = _Sink()

    # When: the direct hardware command rejects the write.
    with pytest.raises(HTTPException) as error:
        await hardware.set_relay_channel_state(
            3,
            hardware.RelayChannelControlRequest(state=1, duration_seconds=60),
            _Relay(False),
            SimpleNamespace(redis_client=redis),
            SimpleNamespace(is_assigned_channel=lambda _channel: False),
            MutationRequestContext.create(),
            sink,
        )

    # Then: neither the Redis override nor a generic success event is fabricated.
    assert error.value.status_code == 503
    assert redis.writes == []
    assert sink.events == []


class _TargetRepository:
    def __init__(self, before: float, succeeds: bool) -> None:
        self.before = before
        self.succeeds = succeeds
        self.committed: list[bool] = []

    async def get_intensity(self, *_args: object) -> float:
        return self.before

    async def set_intensity(self, *_args: object) -> bool:
        if self.succeeds:
            self.committed.append(True)
        return self.succeeds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "succeeds", "expected_events", "status"),
    [(40.0, True, 1, None), (55.0, True, 0, None), (40.0, False, 0, 500)],
)
async def test_device_id_light_target_has_the_same_post_commit_contract(
    monkeypatch: pytest.MonkeyPatch,
    before: float,
    succeeds: bool,
    expected_events: int,
    status: int | None,
) -> None:
    # Given: a registered light with an authoritative target before-image.
    targets = _TargetRepository(before, succeeds)
    database = SimpleNamespace(
        device_repo=SimpleNamespace(
            get_device_type_by_id=_light_type,
            get_light_by_id=_light,
        ),
        room_mode_repo=SimpleNamespace(get_active_mode=_active_mode, get_mode_by_name=_mode),
        light_target_intensity_repo=targets,
    )
    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", _nothing)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", _nothing)
    sink = _Sink()

    # When: the device-ID target entry point changes, repeats, or fails its write.
    try:
        await light_target.update_light_intensity(
            1,
            LightIntensityUpdate(target_intensity=55.0),
            database,
            None,
            MutationRequestContext.create(),
            sink,
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: only a committed target change produces one safe mutation event.
    assert len(sink.events) == expected_events


@pytest.mark.asyncio
async def test_device_id_light_target_validation_failure_is_event_silent() -> None:
    # Given: an invalid target rejected before any repository read or write.
    sink = _Sink()

    # When: the device-ID target endpoint receives an out-of-range value.
    with pytest.raises(HTTPException) as error:
        await light_target.update_light_intensity(
            1,
            LightIntensityUpdate(target_intensity=0.0),
            SimpleNamespace(),
            None,
            MutationRequestContext.create(),
            sink,
        )

    # Then: validation cannot fabricate a persisted mutation event.
    assert error.value.status_code == 400
    assert sink.events == []


async def _light_type(*_args: object) -> str:
    return "light"


async def _light(*_args: object) -> SimpleNamespace:
    return SimpleNamespace(location="Veg Room", cluster="main", device_name="light_1")


async def _active_mode(*_args: object) -> dict[str, str]:
    return {"mode_name": "veg"}


async def _mode(*_args: object) -> dict[str, int]:
    return {"id": 7}


async def _nothing(*_args: object, **_kwargs: object) -> None:
    return None
