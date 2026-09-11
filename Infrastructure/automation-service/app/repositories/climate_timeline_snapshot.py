"""Immutable saved and draft schedule snapshots for climate timeline evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .monitoring_snapshot_types import FrozenRow, frozen, frozen_rows

LOCAL_TZ = ZoneInfo("America/Toronto")


@dataclass(frozen=True, slots=True)
class TimelineWindow:
    """A UTC half-open window with its requested calendar interpretation."""

    start: datetime
    end: datetime
    timezone: str

    @classmethod
    def daily(cls, now: datetime) -> TimelineWindow:
        """Return the Toronto calendar day containing an aware instant."""
        local_day = now.astimezone(LOCAL_TZ).date()
        start = datetime.combine(local_day, time.min, tzinfo=LOCAL_TZ)
        end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
        return cls(start.astimezone(UTC), end.astimezone(UTC), LOCAL_TZ.key)

    @classmethod
    def rolling(cls, now: datetime) -> TimelineWindow:
        """Return exactly 24 elapsed UTC hours beginning at an aware instant."""
        start = now.astimezone(UTC)
        return cls(start, start + timedelta(hours=24), "UTC")


@dataclass(frozen=True, slots=True)
class ClimateSchedule:
    """One exact saved mode/submode configuration and its persisted periods."""

    mode: FrozenRow
    parameters: FrozenRow
    periods: tuple[FrozenRow, ...]


@dataclass(frozen=True, slots=True)
class ClimateScheduleConfiguration:
    """The saved parameters and exact persisted period rows for one mode identity."""

    parameters: FrozenRow
    periods: tuple[FrozenRow, ...]

    @classmethod
    def from_rows(
        cls, parameters: Mapping[str, object], periods: Sequence[Mapping[str, object]]
    ) -> ClimateScheduleConfiguration:
        """Freeze repository rows before they enter immutable schedule snapshots."""
        result = frozen(parameters)
        if result is None:
            raise TypeError("schedule parameters must be present")
        return cls(result, frozen_rows(periods))


@dataclass(frozen=True, slots=True)
class ClimateScheduleSlice:
    """One local calendar-day portion of a requested window."""

    start: datetime
    end: datetime
    schedule: ClimateSchedule | None


@dataclass(frozen=True, slots=True)
class ClimateScheduleSnapshot:
    """Complete saved authority required to evaluate one requested timeline window."""

    window: TimelineWindow
    slices: tuple[ClimateScheduleSlice, ...]


@dataclass(frozen=True, slots=True)
class ClimateScheduleDraft:
    """Validated in-memory fields that overlay one saved mode/submode configuration."""

    mode_id: int
    submode_id: int | None
    periods: tuple[FrozenRow, ...]
    photoperiod: FrozenRow

    @classmethod
    def from_rows(
        cls,
        *,
        mode_id: int,
        submode_id: int | None,
        periods: Sequence[Mapping[str, object]],
        photoperiod: Mapping[str, object],
    ) -> ClimateScheduleDraft:
        """Freeze validated draft rows before they can enter pure evaluation."""
        result = frozen(photoperiod)
        if result is None:
            raise TypeError("photoperiod must be present")
        return cls(mode_id, submode_id, frozen_rows(periods), result)


class ClimateScheduleSnapshotSource(Protocol):
    """Read-only authority required to gather saved schedule configurations."""

    async def read_active_mode(
        self, location: str, cluster: str
    ) -> Mapping[str, object] | None: ...

    async def read_calendar_transition(
        self, location: str, cluster: str, on_date: date
    ) -> Mapping[str, object] | None: ...

    async def read_schedule_configuration(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateScheduleConfiguration | None: ...


class ClimateScheduleSnapshotBuilder:
    """Gather immutable saved schedules and copy them for non-persisting previews."""

    def __init__(self, source: ClimateScheduleSnapshotSource) -> None:
        self._source: ClimateScheduleSnapshotSource = source

    async def build_saved(
        self, location: str, cluster: str, window: TimelineWindow
    ) -> ClimateScheduleSnapshot:
        """Resolve every local calendar day intersecting the requested UTC window."""
        active = await self._source.read_active_mode(location, cluster)
        slices: list[ClimateScheduleSlice] = []
        for local_day in _intersecting_local_days(window):
            slices.append(await self._build_slice(location, cluster, local_day, window, active))
        return ClimateScheduleSnapshot(window, tuple(slices))

    def preview(
        self, saved: ClimateScheduleSnapshot, draft: ClimateScheduleDraft
    ) -> ClimateScheduleSnapshot:
        """Overlay draft fields only on matching copied saved schedule configurations."""
        return replace(
            saved,
            slices=tuple(
                replace(schedule_slice, schedule=_overlay(schedule_slice.schedule, draft))
                for schedule_slice in saved.slices
            ),
        )

    async def _build_slice(
        self,
        location: str,
        cluster: str,
        local_day: date,
        window: TimelineWindow,
        active: Mapping[str, object] | None,
    ) -> ClimateScheduleSlice:
        start, end = _local_day_window(local_day, window)
        transition = await self._source.read_calendar_transition(location, cluster, local_day)
        identity = _resolve_identity(active, transition)
        if identity is None:
            return ClimateScheduleSlice(start, end, None)
        mode_id, submode_id = identity
        configuration = await self._source.read_schedule_configuration(
            location, cluster, mode_id, submode_id
        )
        return ClimateScheduleSlice(start, end, _schedule(configuration, mode_id, submode_id))


def _intersecting_local_days(window: TimelineWindow) -> tuple[date, ...]:
    first = window.start.astimezone(LOCAL_TZ).date()
    last = (window.end - timedelta(microseconds=1)).astimezone(LOCAL_TZ).date()
    return tuple(first + timedelta(days=index) for index in range((last - first).days + 1))


def _local_day_window(local_day: date, window: TimelineWindow) -> tuple[datetime, datetime]:
    day_start = datetime.combine(local_day, time.min, tzinfo=LOCAL_TZ).astimezone(UTC)
    day_end = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=LOCAL_TZ).astimezone(
        UTC
    )
    return max(window.start, day_start), min(window.end, day_end)


def _resolve_identity(
    active: Mapping[str, object] | None, transition: Mapping[str, object] | None
) -> tuple[int, int | None] | None:
    identity = active
    if transition is not None and transition.get("auto_mode_transition") is not False:
        identity = transition
    if identity is None:
        return None
    mode_id = identity.get("target_mode_id", identity.get("mode_id"))
    submode_id = identity.get("target_submode_id", identity.get("submode_id"))
    if not isinstance(mode_id, int) or not (isinstance(submode_id, int) or submode_id is None):
        return None
    return mode_id, submode_id


def _schedule(
    configuration: ClimateScheduleConfiguration | None, mode_id: int, submode_id: int | None
) -> ClimateSchedule | None:
    if configuration is None:
        return None
    return ClimateSchedule(
        frozen({"mode_id": mode_id, "submode_id": submode_id}) or FrozenRow(()),
        configuration.parameters,
        configuration.periods,
    )


def _overlay(
    schedule: ClimateSchedule | None, draft: ClimateScheduleDraft
) -> ClimateSchedule | None:
    if schedule is None:
        return None
    if schedule.mode.get("mode_id") != draft.mode_id:
        return schedule
    if schedule.mode.get("submode_id") != draft.submode_id:
        return schedule
    parameters = frozen({**dict(schedule.parameters), **dict(draft.photoperiod)})
    if parameters is None:
        return schedule
    return ClimateSchedule(schedule.mode, parameters, draft.periods)
