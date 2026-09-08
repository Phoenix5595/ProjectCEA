from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.routes.climate_periods import delete_climate_periods
from app.routes.schedules import base, room
from app.schemas.schedules import ScheduleUpdate


class _Sink:
    def __init__(self, committed: list[bool]) -> None:
        self._committed = committed
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        assert self._committed == [True]
        self.events.append(event)


class _ScheduleRepository:
    def __init__(self, before: dict[str, object], after: dict[str, object] | None) -> None:
        self.before = before
        self.after = after
        self.committed: list[bool] = []

    async def get_schedules(self, *_args: object) -> list[dict[str, object]]:
        return [self.after if self.committed and self.after is not None else self.before]

    async def update_schedule(self, *_args: object, **_kwargs: object) -> dict[str, object] | None:
        if self.after is None:
            return None
        self.committed.append(True)
        return self.after

    async def delete_schedule(self, _schedule_id: int) -> bool:
        if self.after is None:
            return False
        self.committed.append(True)
        return True


def _schedule(start_time: str = "06:00") -> dict[str, object]:
    return {
        "id": 7,
        "name": "Day",
        "location": "Veg Room",
        "cluster": "main",
        "start_time": start_time,
        "end_time": "18:00",
        "enabled": True,
        "mode": "DAY",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("after", "expected_events", "status"),
    [(_schedule("07:00"), 1, None), (_schedule(), 0, None), (None, 0, 500)],
)
async def test_schedule_update_is_post_commit_and_silent_for_noop_or_failure(
    after: dict[str, object] | None, expected_events: int, status: int | None
) -> None:
    # Given: a schedule repository with a safe route-local before image.
    repository = _ScheduleRepository(_schedule(), after)
    sink = _Sink(repository.committed)

    # When: a changed, identical, or failed update is requested.
    try:
        await base.update_schedule(
            7,
            ScheduleUpdate(start_time="07:00"),
            SimpleNamespace(schedule_repo=repository),
            None,
            None,
            MutationRequestContext.create(),
            sink,
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: only a committed field change creates one safe event.
    assert len(sink.events) == expected_events
    if expected_events:
        assert [change.key for change in sink.events[0].payload.changes] == ["start_time"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("after", "expected_events", "status"), [({}, 1, None), (None, 0, 404)])
async def test_schedule_delete_emits_only_after_a_successful_delete(
    after: dict[str, object] | None, expected_events: int, status: int | None
) -> None:
    # Given: an existing schedule and a delete result.
    repository = _ScheduleRepository(_schedule(), after)
    sink = _Sink(repository.committed)

    # When: deletion succeeds or the repository reports no persisted row.
    try:
        await base.delete_schedule(
            7, SimpleNamespace(schedule_repo=repository), MutationRequestContext.create(), sink
        )
    except HTTPException as error:
        assert error.status_code == status

    # Then: only the committed delete has one safe before-to-empty diff.
    assert len(sink.events) == expected_events


class _ClimateRepository:
    def __init__(self, previous: list[dict[str, object]], result: bool) -> None:
        self.previous = previous
        self.result = result
        self.committed: list[bool] = []

    async def get_periods(self, *_args: object) -> list[dict[str, object]]:
        return self.previous

    async def delete_periods(self, *_args: object) -> bool:
        if self.result:
            self.committed.append(True)
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "result", "expected_events"),
    [([{"id": 1}], True, 1), ([], True, 0), ([{"id": 1}], False, 0)],
)
async def test_climate_delete_is_silent_without_a_changed_committed_result(
    previous: list[dict[str, object]], result: bool, expected_events: int
) -> None:
    # Given: persisted climate rows or an empty/failing delete boundary.
    repository = _ClimateRepository(previous, result)
    sink = _Sink(repository.committed)

    # When: the route deletes the room's periods.
    await delete_climate_periods(
        "Veg Room",
        "main",
        SimpleNamespace(climate_periods_repo=repository),
        MutationRequestContext.create(),
        sink,
    )

    # Then: no-op and database-failure outcomes produce no fake event.
    assert len(sink.events) == expected_events


@pytest.mark.asyncio
async def test_room_schedule_sync_forwards_one_context_and_sink_to_the_persisted_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: active mode parameters and a save boundary that emits after its commit.
    observed: list[tuple[MutationRequestContext, _Sink]] = []
    context = MutationRequestContext.create()
    sink = _Sink([True])

    async def save(*args: object) -> dict[str, object]:
        observed.append((args[-2], args[-1]))
        return {"success": True, "schedules_created": 2}

    monkeypatch.setattr(room, "save_room_schedule", save)
    database = SimpleNamespace(
        room_mode_repo=SimpleNamespace(
            get_active_mode=_active_mode,
            get_mode_parameters=_mode_parameters,
        )
    )

    # When: the sync entry point resolves mode parameters.
    response = await room.sync_room_schedule_from_mode_parameters(
        "Veg Room", "main", database, SimpleNamespace(), context, sink
    )

    # Then: it delegates exactly once to the sole persisted mutation boundary.
    assert response["success"] is True
    assert observed == [(context, sink)]


@pytest.mark.asyncio
async def test_room_schedule_sync_validation_failure_is_event_silent() -> None:
    # Given: a room without an active persisted mode.
    sink = _Sink([])
    database = SimpleNamespace(
        room_mode_repo=SimpleNamespace(
            get_active_mode=_no_active_mode,
            get_mode_parameters=_mode_parameters,
        )
    )

    # When: the sync entry point cannot resolve its authoritative source.
    with pytest.raises(HTTPException) as error:
        await room.sync_room_schedule_from_mode_parameters(
            "Veg Room", "main", database, SimpleNamespace(), MutationRequestContext.create(), sink
        )

    # Then: validation failure emits no persisted-mutation event.
    assert error.value.status_code == 404
    assert sink.events == []


async def _active_mode(*_args: object) -> dict[str, str]:
    return {"mode_name": "veg", "submode_name": None}


async def _mode_parameters(*_args: object) -> dict[str, object]:
    return {"day_start_time": "06:00", "night_start_time": "18:00"}


async def _no_active_mode(*_args: object) -> None:
    return None
