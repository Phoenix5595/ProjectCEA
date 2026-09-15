from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

from app.background_tasks.calendar import CalendarMixin
from app.container import ServiceContainer
from app.database import DatabaseManager
from app.events.mutation_dependencies import NoopOperationalEventSink
from app.events.operational_models import OperationalEvent, SystemPayload
from app.repositories.calendar import CalendarRepository
from app.repositories.room_modes import RoomModeRepository
from app.redis_client import AutomationRedisClient
from app.services.calendar_mode_scheduler import CalendarModeScheduler


class _CalendarRepository(CalendarRepository):
    def __init__(self) -> None:
        super().__init__(Mock())

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return True

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, object]:
        del on_date
        return {"id": 7, "metadata": {"target_mode_name": "missing"}}

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        del event_id, applied_date
        return False


class _RoomModeRepository(RoomModeRepository):
    def __init__(self) -> None:
        super().__init__(Mock())

    async def get_room_mode_by_name(self, name: str) -> None:
        del name
        return None


class _CalendarDatabase(DatabaseManager):
    def __init__(self) -> None:
        super().__init__({"host": "localhost", "port": 5432, "database": "test", "user": "test"})
        self._calendar_repo = _CalendarRepository()
        self._room_mode_repo = _RoomModeRepository()


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


class _FailingSink:
    def emit_nowait(self, event: OperationalEvent) -> None:
        del event
        raise RuntimeError("event dispatch unavailable")


class _CalendarWorker(CalendarMixin):
    def __init__(self, database: DatabaseManager, event_sink: _RecordingSink) -> None:
        self.database = database
        self.operational_event_sink = event_sink
        self._calendar_scheduler = None
        self._last_calendar_mode_tick = 0.0
        self._calendar_mode_interval = 0.0


def _calendar_database() -> DatabaseManager:
    return _CalendarDatabase()


@pytest.mark.asyncio
async def test_operational_events_use_one_noop_sink_when_redis_is_unavailable() -> None:
    # Given: initialized state Redis reports an unavailable connection.
    container = ServiceContainer()
    container.automation_redis = AutomationRedisClient(redis_url="redis://localhost:0")

    # When: operational runtime composition is attempted.
    await container._compose_operational_events()

    # Then: producers retain one safe no-op sink without a reader or dispatcher.
    assert isinstance(container.get_operational_event_sink(), NoopOperationalEventSink)
    assert container.operational_event_dispatcher is None
    assert container.operational_event_reader is None


@pytest.mark.asyncio
async def test_calendar_worker_forwards_sink_and_isolates_skip_event_dispatch() -> None:
    # Given: a worker whose due Flower destination cannot resolve and a failing sink.
    recording_sink = _RecordingSink()
    worker = _CalendarWorker(_calendar_database(), recording_sink)

    # When: the periodic worker reaches the same transition twice and direct dispatch fails.
    await worker._maybe_run_calendar_mode_scheduler()
    await worker._maybe_run_calendar_mode_scheduler()
    await CalendarModeScheduler(_calendar_database(), _FailingSink())._apply_for_date(
        date(2026, 3, 8), "calendar_scheduler"
    )

    # Then: one complete warning is queued and a sink outage cannot interrupt the scheduler.
    assert [event.event_type for event in recording_sink.events] == ["calendar.transition_skipped"]
    assert recording_sink.events[0].reason_code == "unknown_mode"
    match recording_sink.events[0].payload:
        case SystemPayload(details=details):
            assert [(detail.key, detail.value) for detail in details] == [
                ("phase", "7"),
                ("destination", "missing/default"),
                ("resolution_reason", "unknown_mode"),
            ]
        case payload:
            raise AssertionError(f"unexpected payload: {payload.family}")
