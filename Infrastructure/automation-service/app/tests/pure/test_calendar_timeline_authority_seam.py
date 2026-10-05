from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, final
from unittest.mock import Mock

import pytest

from app.database import DatabaseManager
from app.events.operational_models import OperationalEvent
from app.monitoring_publication.rich import project_saved_trajectory
from app.repositories.calendar import CalendarRepository
from app.repositories.climate_periods import ClimatePeriodRepository
from app.repositories.climate_timeline_snapshot import (
    ClimateScheduleSnapshot,
    ClimateScheduleSnapshotBuilder,
    TimelineWindow,
)
from app.repositories.monitoring_snapshot_sources import SavedTrajectorySnapshotSource
from app.repositories.room_modes import RoomModeRepository
from app.services.calendar_mode_scheduler import CalendarModeScheduler


@final
class CalendarRepositoryFake(CalendarRepository):
    def __init__(self, enabled: bool, event: Mapping[str, object] | None) -> None:
        super().__init__(Mock())
        self._enabled = enabled
        self._event = event

    async def flower_calendar_mode_transitions_enabled(self) -> bool:
        return self._enabled

    async def get_active_flower_phase_event(self, on_date: date) -> dict[str, Any] | None:
        del on_date
        return None if self._event is None else dict(self._event)

    async def mode_application_exists(self, event_id: int, applied_date: date) -> bool:
        del event_id, applied_date
        return False


@final
class RoomModeRepositoryFake(RoomModeRepository):
    def __init__(self) -> None:
        super().__init__(Mock())
        self._active = {"mode_id": 1, "submode_id": None, "mode_name": "veg"}

    async def get_active_mode(self, location: str, cluster: str) -> dict[str, Any] | None:
        del location, cluster
        return self._active

    async def get_room_mode_by_name(self, name: str) -> dict[str, Any] | None:
        return {"id": 2, "name": "flower"} if name == "flower" else None

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        return [{"id": 4, "name": "bulk"}]


@final
class ClimatePeriodsRepositoryFake(ClimatePeriodRepository):
    def __init__(self) -> None:
        super().__init__(Mock())

    async def get_periods_for_room_mode(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> list[dict[str, Any]]:
        del location, cluster, submode_id
        return [
            {
                "id": mode_id * 10,
                "period_name": "Day",
                "start_time": "06:00",
                "end_time": "18:00",
                "heating_setpoint": 21.0,
            }
        ]


@final
class ConnectionFake:
    def __init__(self, configured: bool) -> None:
        self._configured = configured

    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None:
        del query
        if not self._configured:
            return None
        mode_id = args[2]
        assert isinstance(mode_id, int)
        return {
            "mode_id": mode_id,
            "day_start_time": "06:00",
            "night_start_time": "18:00",
        }


@final
class AcquireFake:
    def __init__(self, configured: bool) -> None:
        self._configured = configured

    async def __aenter__(self) -> ConnectionFake:
        return ConnectionFake(self._configured)

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback


@final
class PoolFake:
    def __init__(self, configured: bool = True) -> None:
        self._configured = configured

    def acquire(self) -> AcquireFake:
        return AcquireFake(self._configured)


@final
class DatabaseFake(DatabaseManager):
    def __init__(self, enabled: bool, event: Mapping[str, object] | None) -> None:
        super().__init__({"host": "localhost", "port": 5432, "database": "test", "user": "test"})
        self._calendar_repo = CalendarRepositoryFake(enabled, event)
        self._room_mode_repo = RoomModeRepositoryFake()
        self._climate_periods_repo = ClimatePeriodsRepositoryFake()


@final
class RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


@final
class FailingSink:
    def emit_nowait(self, event: OperationalEvent) -> None:
        del event
        raise RuntimeError("event dispatch unavailable")


async def _build_snapshot(
    *, location: str, enabled: bool, event: Mapping[str, object] | None, configured: bool = True
) -> ClimateScheduleSnapshot:
    database = DatabaseFake(enabled, event)
    source = SavedTrajectorySnapshotSource(
        database.room_mode_repo,
        database.climate_periods_repo,
        CalendarModeScheduler(database),
        PoolFake(configured),
    )
    return await ClimateScheduleSnapshotBuilder(source).build_saved(
        location,
        "main",
        TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC)),
    )


async def _build_schedule(
    *, location: str, enabled: bool, event: Mapping[str, object] | None
) -> tuple[int, int | None] | None:
    snapshot = await _build_snapshot(location=location, enabled=enabled, event=event)
    schedule = snapshot.slices[0].schedule
    if schedule is None:
        return None
    return schedule.mode["mode_id"], schedule.mode["submode_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "enabled", "event", "expected"),
    (
        (
            "Flower Room",
            True,
            {"id": 7, "metadata": {"target_mode_name": "flower", "target_submode_name": "bulk"}},
            (2, 4),
        ),
        ("Flower Room", False, {"id": 7, "metadata": {"target_mode_name": "flower"}}, (1, None)),
        ("Flower Room", True, None, (1, None)),
        ("Veg Room", True, {"id": 7, "metadata": {"target_mode_name": "flower"}}, (1, None)),
    ),
)
async def test_saved_timeline_composition_uses_calendar_only_when_it_has_authority(
    location: str,
    enabled: bool,
    event: Mapping[str, object] | None,
    expected: tuple[int, int | None],
) -> None:
    # Given: real scheduler and snapshot composition with only database-boundary fakes.

    # When: a saved timeline is built for a transition-day authority condition.
    schedule_identity = await _build_schedule(location=location, enabled=enabled, event=event)

    # Then: the resolved destination or the active schedule remains usable as contracted.
    assert schedule_identity == expected


@pytest.mark.asyncio
async def test_saved_timeline_falls_back_to_active_schedule_and_warns_for_unknown_destination() -> (
    None
):
    # Given: an authoritative Flower phase whose target mode cannot resolve.
    snapshot = await _build_snapshot(
        location="Flower Room",
        enabled=True,
        event={"id": 7, "metadata": {"target_mode_name": "missing"}},
    )

    # When: the saved projection is built from the fallback schedule.
    trajectory = project_saved_trajectory(snapshot, "Flower Room", "0000001")

    # Then: active authority remains visible with a stable skipped-transition warning.
    assert snapshot.slices[0].schedule is not None
    assert snapshot.slices[0].schedule.mode["mode_id"] == 1
    assert trajectory is not None
    daytime = next(
        segment
        for segment in trajectory.segments
        if segment.metric == "heating"
        and segment.trajectory_kind == "scheduled"
        and segment.source.period.period_id == "10"
        and segment.shape == "step"
    )
    assert (daytime.start, daytime.end) == (
        datetime(2026, 3, 8, 10, tzinfo=UTC),
        datetime(2026, 3, 8, 22, tzinfo=UTC),
    )
    assert [(warning.code, warning.detail) for warning in trajectory.warnings] == [
        ("calendar.transition_skipped", "calendar.transition_skipped:unknown_mode")
    ]


@pytest.mark.asyncio
async def test_scheduler_skips_unknown_destination_once_and_isolates_sink_failure() -> None:
    # Given: a due calendar transition whose destination mode cannot resolve.
    event = {"id": 7, "metadata": {"target_mode_name": "missing"}}
    recording_sink = RecordingSink()
    scheduler = CalendarModeScheduler(DatabaseFake(True, event), recording_sink)

    # When: repeated scheduler ticks reach the same due transition.
    await scheduler._apply_for_date(date(2026, 3, 8), "calendar_scheduler")
    await scheduler._apply_for_date(date(2026, 3, 8), "calendar_scheduler")
    await CalendarModeScheduler(DatabaseFake(True, event), FailingSink())._apply_for_date(
        date(2026, 3, 8), "calendar_scheduler"
    )

    # Then: one warning event records the skip and a sink failure cannot stop the scheduler.
    assert [event.event_type for event in recording_sink.events] == ["calendar.transition_skipped"]
    assert recording_sink.events[0].severity.value == "warning"


@pytest.mark.asyncio
async def test_saved_timeline_remains_unavailable_when_active_schedule_is_unconfigured() -> None:
    # Given: no calendar authority and no persisted configuration for the active mode.
    snapshot = await _build_snapshot(
        location="Flower Room", enabled=False, event=None, configured=False
    )

    # When: the saved schedule authority is gathered.
    schedule = snapshot.slices[0].schedule

    # Then: the unrecoverable active-mode configuration gap remains explicit.
    assert schedule is None
