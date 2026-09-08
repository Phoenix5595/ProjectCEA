from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes.lights import light_status, light_target
from app.routes.schedules import base as schedule_routes
from app.schemas.lights import ScheduleTimeControl, TargetIntensityControl
from app.schemas.schedules import ScheduleCreate


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _TargetRepository:
    def __init__(self, before: float, write_succeeds: bool = True) -> None:
        self.before = before
        self.write_succeeds = write_succeeds
        self.writes: list[float] = []

    async def get_intensity(self, _device_id: int, _mode_id: int) -> float:
        return self.before

    async def set_intensity(self, _device_id: int, _mode_id: int, value: float) -> bool:
        self.writes.append(value)
        return self.write_succeeds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "write_succeeds", "expected_events"),
    [(40.0, True, 1), (55.0, True, 0), (40.0, False, 0)],
)
async def test_light_target_emits_only_for_a_changed_persisted_value(
    monkeypatch: pytest.MonkeyPatch,
    before: float,
    write_succeeds: bool,
    expected_events: int,
) -> None:
    # Given: a route-local target repository with a known prior target.
    targets = _TargetRepository(before, write_succeeds)
    database = SimpleNamespace(
        device_repo=SimpleNamespace(get_device_id=_device_id),
        room_mode_repo=SimpleNamespace(
            get_active_mode=_active_mode,
            get_mode_by_name=_mode_by_name,
        ),
        light_target_intensity_repo=targets,
    )
    config = SimpleNamespace(get_devices=_devices)
    context = MutationRequestContext(correlation_id=UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"))
    sink = _RecordingSink()
    monkeypatch.setattr(light_target, "_sync_scheduler_light_intensities", _do_nothing)
    monkeypatch.setattr(light_target, "_publish_schedule_changed", _do_nothing)

    # When: the requested target either changes, repeats, or fails persistence.
    if write_succeeds:
        await light_target.set_target_intensity(
            "Veg Room",
            "main",
            "light_1",
            TargetIntensityControl(target_intensity=55.0),
            config,
            database,
            None,
            context,
            sink,
        )
    else:
        with pytest.raises(HTTPException) as error:
            await light_target.set_target_intensity(
                "Veg Room",
                "main",
                "light_1",
                TargetIntensityControl(target_intensity=55.0),
                config,
                database,
                None,
                context,
                sink,
            )
        assert error.value.detail == "Failed to set light target intensity for light_1"

    # Then: only a committed value change reaches the operational sink.
    assert len(sink.events) == expected_events
    if expected_events:
        event = sink.events[0]
        assert event.correlation_id == context.correlation_id
        assert event.entity.entity_id == "1:7"
        assert event.payload.changes[0].before == before
        assert event.payload.changes[0].after == 55.0


class _ScheduleRepository:
    def __init__(self, rows: list[dict[str, object]], write_succeeds: bool = True) -> None:
        self.rows = rows
        self.write_succeeds = write_succeeds

    async def get_schedules(self, *_args: object) -> list[dict[str, object]]:
        return self.rows

    async def create_schedule(self, *_args: object) -> int | None:
        return 9 if self.write_succeeds else None

    async def update_schedule(self, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        return self.rows[0] if self.write_succeeds else None

    async def delete_schedule(self, _schedule_id: int) -> bool:
        return self.write_succeeds


@pytest.mark.asyncio
async def test_schedule_create_emits_a_correlated_safe_diff_after_persistence() -> None:
    # Given: a repository that returns the committed schedule row.
    created = {
        "id": 9,
        "name": "Day",
        "location": "Veg Room",
        "cluster": "main",
        "device_name": "light_1",
        "start_time": "06:00",
        "end_time": "18:00",
        "enabled": True,
        "mode": "DAY",
    }
    sink = _RecordingSink()
    context = MutationRequestContext.create()
    database = SimpleNamespace(schedule_repo=_ScheduleRepository([created]))

    # When: the create route returns after repository persistence.
    await schedule_routes.create_schedule(
        ScheduleCreate(
            name="Day",
            location="Veg Room",
            cluster="main",
            device_name="light_1",
            start_time="06:00",
            end_time="18:00",
            mode="DAY",
        ),
        database,
        context,
        sink,
    )

    # Then: one correlated event contains only allowlisted schedule fields.
    assert len(sink.events) == 1
    assert sink.events[0].correlation_id == context.correlation_id
    assert sink.events[0].entity.entity_id == "9"
    assert {change.key for change in sink.events[0].payload.changes} == {
        "enabled",
        "end_time",
        "mode",
        "name",
        "start_time",
    }


@pytest.mark.asyncio
async def test_light_schedule_update_suppresses_noop_and_failure_events() -> None:
    # Given: a route with an unchanged persisted schedule followed by a failed write.
    original = {"id": 5, "start_time": "06:00", "end_time": "18:00"}
    sink = _RecordingSink()
    database = SimpleNamespace(
        schedule_repo=SimpleNamespace(get_room_light_schedule=_room_light_schedule),
        update_light_schedule_times=_unchanged_light_schedule,
    )

    # When: the persisted result is unchanged.
    await light_status.update_light_schedule(
        "Veg Room",
        "main",
        "light_1",
        ScheduleTimeControl(start_time="06:00", end_time="18:00"),
        SimpleNamespace(),
        database,
        None,
        MutationRequestContext.create(),
        sink,
    )

    # Then: no misleading event reaches the sink.
    assert sink.events == []
    assert original["id"] == 5


async def _room_light_schedule(*_args: object) -> dict[str, object]:
    return {"id": 5, "start_time": "06:00", "end_time": "18:00"}


async def _unchanged_light_schedule(*_args: object) -> bool:
    return True


async def _device_id(*_args: object) -> int:
    return 1


async def _active_mode(*_args: object) -> dict[str, str]:
    return {"mode_name": "veg"}


async def _mode_by_name(*_args: object) -> dict[str, int]:
    return {"id": 7}


async def _devices() -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    return {"Veg Room": {"main": {"light_1": {"device_type": "light"}}}}


async def _do_nothing(*_args: object, **_kwargs: object) -> None:
    return None
