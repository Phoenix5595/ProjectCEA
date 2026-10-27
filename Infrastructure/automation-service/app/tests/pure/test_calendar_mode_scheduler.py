from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, final
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.database import DatabaseManager
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.repositories.calendar import CalendarRepository as CalendarRepositoryBase
from app.routes.calendar import core as calendar_core
from app.schemas.calendar import FlowerCalendarModeTransitionUpdate
from app.services.calendar_mode_scheduler import CalendarModeScheduler


@final
class SchedulerCalendarRepository(CalendarRepositoryBase):
    def __init__(self, transitions_enabled: bool) -> None:
        super().__init__(Mock())
        self._transitions_enabled: bool = transitions_enabled
        self.mode_application_checked: bool = False
        self.current_run_reads: int = 0

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return self._transitions_enabled

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, object]:
        del on_date
        return {"id": 7, "metadata": {"target_mode_name": "flower"}}

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        del event_id, applied_date
        self.mode_application_checked = True
        return self._transitions_enabled

    async def get_current_run(self, location: str, today: date | None = None):
        del location, today
        self.current_run_reads += 1
        return None


@final
class CalendarDatabase(DatabaseManager):
    def __init__(
        self,
        calendar_repo: CalendarRepositoryBase,
        room_mode_repo: _RecordingRoomModeRepository | None = None,
    ) -> None:
        super().__init__(
            {
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "user": "test",
                "password": "test",
            }
        )
        self._calendar_repo = calendar_repo
        self._room_mode_repo = room_mode_repo or _RecordingRoomModeRepository(
            {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
        )


@final
class CalendarModeTransitionSettingRepository(CalendarRepositoryBase):
    def __init__(self) -> None:
        super().__init__(Mock())
        self.enabled: bool = True

    async def set_flower_calendar_mode_transitions_enabled(
        self, enabled: bool
    ) -> dict[str, object]:
        self.enabled = enabled
        return {"calendar_mode_transitions_enabled": enabled}

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return self.enabled


@final
class RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_calendar_scheduler_skips_flower_transition_when_room_control_is_disabled() -> None:
    # Given: Flower calendar control is disabled despite an active transition event.
    repository = SchedulerCalendarRepository(transitions_enabled=False)
    scheduler = CalendarModeScheduler(CalendarDatabase(repository))

    # When: the scheduler reaches the Flower transition day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: no calendar metadata is consulted as an authority or recorded as an application.
    assert repository.mode_application_checked is False


@pytest.mark.asyncio
async def test_calendar_scheduler_keeps_calendar_transition_behavior_when_room_control_is_enabled() -> (
    None
):
    # Given: Flower calendar control remains enabled by default.
    repository = SchedulerCalendarRepository(transitions_enabled=True)
    scheduler = CalendarModeScheduler(CalendarDatabase(repository))

    # When: the scheduler reaches the Flower transition day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the existing calendar application path is still reached.
    assert repository.mode_application_checked is True


@pytest.mark.asyncio
async def test_flower_calendar_mode_transition_setting_persists_the_requested_value() -> None:
    # Given: Flower calendar transitions begin enabled in the persistent repository.
    repository = CalendarModeTransitionSettingRepository()
    database = CalendarDatabase(repository)
    sink = RecordingSink()

    # When: the Flower-only setting is disabled through the calendar API handler.
    response = await calendar_core.update_flower_mode_transitions(
        FlowerCalendarModeTransitionUpdate(enabled=False),
        database,
        MutationRequestContext.create(),
        sink,
    )

    # Then: the committed response and repository agree on the disabled value.
    assert response == {"enabled": False}
    assert repository.enabled is False
    assert len(sink.events) == 1


@final
class _RecordingRoomModeRepository:
    """Catalogue lookups plus a configured active identity."""

    def __init__(self, active: dict[str, Any] | None) -> None:
        self.active = active

    async def get_room_mode_by_name(self, name: str) -> dict[str, Any] | None:
        catalogue = {"veg": 1, "flower": 2, "drying": 3}
        if name not in catalogue:
            return None
        return {"id": catalogue[name], "name": name}

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        return [{"id": 1, "name": "stretch"}, {"id": 2, "name": "bulk"}]

    async def get_active_mode(self, location: str, cluster: str) -> dict[str, Any] | None:
        del location, cluster
        return self.active


@final
class _RecordingCalendarRepository(CalendarRepositoryBase):
    """One active Flower phase event with recorded mode applications."""

    def __init__(
        self,
        event: dict[str, Any] | None,
        *,
        transitions_enabled: bool = True,
        run: Any = None,
    ) -> None:
        super().__init__(Mock())
        self.event = event
        self.transitions_enabled = transitions_enabled
        self.run = run
        self.applications: list[tuple[int, date, int | None, int | None, str]] = []
        self.current_run_reads: list[tuple[str, date | None]] = []
        self.alignments: list[tuple[Any, date, bool]] = []

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return self.transitions_enabled

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, Any] | None:
        del on_date
        return self.event

    async def get_current_run(self, location: str, today: date | None = None) -> Any:
        self.current_run_reads.append((location, today))
        return self.run

    async def align_flower_start(
        self, grow_plan_id: Any, flower_start: date, *, forward_only: bool
    ) -> list[dict[str, Any]] | None:
        self.alignments.append((grow_plan_id, flower_start, forward_only))
        return []

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        del event_id, applied_date
        return False

    async def record_mode_application(
        self,
        event_id: int,
        applied_date: date,
        mode_id: int | None,
        submode_id: int | None,
        triggered_by: str,
    ) -> None:
        self.applications.append((event_id, applied_date, mode_id, submode_id, triggered_by))


@final
class _CalendarModeDatabase(DatabaseManager):
    def __init__(
        self,
        calendar_repo: _RecordingCalendarRepository,
        room_mode_repo: _RecordingRoomModeRepository,
    ) -> None:
        super().__init__(
            {
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "user": "test",
                "password": "test",
            }
        )
        self._calendar_repo = calendar_repo
        self._room_mode_repo = room_mode_repo


@final
class _StubTransitionService:
    """Configurable transition outcome with recorded activation calls."""

    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, int, int | None, str]] = []

    async def execute_mode_transition(
        self,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
        triggered_by: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.calls.append((location, cluster, mode_id, submode_id, triggered_by))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


_BULK_PHASE_EVENT: dict[str, Any] = {
    "id": 7,
    "event_type": "phase",
    "title": "Bulk",
    "metadata": {
        "auto_mode_transition": True,
        "target_mode_name": "flower",
        "target_submode_name": "bulk",
    },
}


@pytest.mark.asyncio
async def test_calendar_scheduler_does_not_record_a_returned_failed_transition() -> None:
    # Given: the transition service reports the activation did not commit.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
    )
    transition = _StubTransitionService(
        result={"success": False, "message": "schedule sync failed"}
    )
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler reaches the Flower phase day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the failed transition is attempted as a calendar change but not recorded.
    assert transition.calls == [("Flower Room", "main", 2, 2, "system")]
    assert repository.applications == []


@pytest.mark.asyncio
async def test_calendar_scheduler_does_not_record_a_thrown_transition_failure() -> None:
    # Given: the transition service raises instead of returning a result.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
    )
    transition = _StubTransitionService(error=RuntimeError("registry unavailable"))
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler reaches the Flower phase day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: nothing is recorded as applied.
    assert transition.calls == [("Flower Room", "main", 2, 2, "system")]
    assert repository.applications == []


@pytest.mark.asyncio
async def test_calendar_scheduler_records_same_active_destination_without_transition() -> None:
    # Given: the running identity already matches the calendar destination.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 2, "mode_name": "flower"}
    )
    transition = _StubTransitionService(result={"success": True})
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler reaches the Flower phase day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: no activation runs, but the application stays recorded for the day.
    assert transition.calls == []
    assert repository.applications == [(7, date(2026, 3, 8), 2, 2, "calendar_scheduler")]


@pytest.mark.asyncio
async def test_calendar_scheduler_mutation_requires_the_injected_transition_service() -> None:
    # Given: a calendar destination must change mode without an injected service.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
    )
    scheduler = CalendarModeScheduler(_CalendarModeDatabase(repository, room_modes))

    # When: the scheduler reaches the Flower phase day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the missing service fails the application explicitly without recording.
    assert repository.applications == []


_STRETCH_PHASE_EVENT: dict[str, Any] = {
    "id": 9,
    "event_type": "flower_stretch",
    "title": "Stretch",
    "metadata": {
        "auto_mode_transition": True,
        "target_mode_name": "flower",
        "target_submode_name": "stretch",
    },
}


@pytest.mark.asyncio
async def test_calendar_scheduler_applies_a_same_mode_submode_change() -> None:
    # Given: Flower/stretch is active and the bulk phase begins today.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
    )
    transition = _StubTransitionService(result={"success": True})
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler reaches the bulk day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the submode changes inside the active mode and the day is recorded.
    assert transition.calls == [("Flower Room", "main", 2, 2, "system")]
    assert repository.applications == [(7, date(2026, 3, 8), 2, 2, "calendar_scheduler")]


@pytest.mark.asyncio
async def test_calendar_scheduler_leaves_a_cross_mode_change_to_the_owner() -> None:
    # Given: Veg is still active while the calendar has reached stretch.
    repository = _RecordingCalendarRepository(_STRETCH_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 1, "submode_id": None, "mode_name": "veg"}
    )
    transition = _StubTransitionService(result={"success": True})
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler ticks on the stretch day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: nothing activates and nothing is recorded; the owner flips manually.
    assert transition.calls == []
    assert repository.applications == []


@pytest.mark.asyncio
async def test_calendar_scheduler_leaves_drying_to_veg_to_the_owner() -> None:
    # Given: the plan ended and Drying is still active with no calendar event.
    repository = _RecordingCalendarRepository(None)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 3, "submode_id": None, "mode_name": "drying"}
    )
    transition = _StubTransitionService(result={"success": True})
    scheduler = CalendarModeScheduler(
        _CalendarModeDatabase(repository, room_modes),
        transition_service=transition,
    )

    # When: the scheduler ticks after the plan.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: no automatic Drying -> Veg transition exists any more.
    assert transition.calls == []
    assert repository.applications == []


@pytest.mark.asyncio
async def test_calendar_scheduler_extends_waiting_bed_veg_with_transitions_disabled() -> None:
    # Given: Veg is active with a waiting plan while calendar transitions are off.
    run = SimpleNamespace(grow_plan_id=uuid4())
    repository = _RecordingCalendarRepository(None, transitions_enabled=False, run=run)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 1, "submode_id": None, "mode_name": "veg"}
    )
    scheduler = CalendarModeScheduler(_CalendarModeDatabase(repository, room_modes))

    # When: the scheduler ticks.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the waiting bed veg is aligned forward before the toggle gate.
    assert repository.alignments == [(run.grow_plan_id, date(2026, 3, 8), True)]


@pytest.mark.asyncio
async def test_calendar_scheduler_does_not_read_the_plan_while_flower_is_active() -> None:
    # Given: Flower is active, so bed veg is no longer waiting.
    repository = _RecordingCalendarRepository(_BULK_PHASE_EVENT)
    room_modes = _RecordingRoomModeRepository(
        {"mode_id": 2, "submode_id": 1, "mode_name": "flower"}
    )
    scheduler = CalendarModeScheduler(_CalendarModeDatabase(repository, room_modes))

    # When: the scheduler ticks.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the plan is never read and never aligned.
    assert repository.current_run_reads == []
    assert repository.alignments == []
