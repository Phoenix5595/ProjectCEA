from __future__ import annotations

from datetime import date
from typing import Any, final
from unittest.mock import Mock

import pytest

from app.database import DatabaseManager
from app.events.mutation_context import MutationRequestContext
from app.events.operational_models import OperationalEvent
from app.repositories.calendar import CalendarRepository as CalendarRepositoryBase
from app.routes import calendar
from app.schemas.calendar import FlowerCalendarModeTransitionUpdate
from app.services.calendar_mode_scheduler import CalendarModeScheduler


@final
class SchedulerCalendarRepository(CalendarRepositoryBase):
    def __init__(self, transitions_enabled: bool) -> None:
        super().__init__(Mock())
        self._transitions_enabled: bool = transitions_enabled
        self.mode_application_checked: bool = False

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return self._transitions_enabled

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, object]:
        del on_date
        return {"id": 7, "metadata": {"target_mode_name": "flower"}}

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        del event_id, applied_date
        self.mode_application_checked = True
        return self._transitions_enabled


@final
class CalendarDatabase(DatabaseManager):
    def __init__(self, calendar_repo: CalendarRepositoryBase) -> None:
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
    response = await calendar.update_flower_mode_transitions(
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

    def __init__(self, event: dict[str, Any]) -> None:
        super().__init__(Mock())
        self.event = event
        self.applications: list[tuple[int, date, int | None, int | None, str]] = []

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return True

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, Any]:
        del on_date
        return self.event

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
    room_modes = _RecordingRoomModeRepository({"mode_id": 2, "submode_id": 1})
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
    room_modes = _RecordingRoomModeRepository({"mode_id": 2, "submode_id": 1})
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
    room_modes = _RecordingRoomModeRepository({"mode_id": 2, "submode_id": 2})
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
    room_modes = _RecordingRoomModeRepository({"mode_id": 2, "submode_id": 1})
    scheduler = CalendarModeScheduler(_CalendarModeDatabase(repository, room_modes))

    # When: the scheduler reaches the Flower phase day.
    await scheduler._apply_for_date(date(2026, 3, 8), triggered_by="calendar_scheduler")

    # Then: the missing service fails the application explicitly without recording.
    assert repository.applications == []
