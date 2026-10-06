from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import final

import pytest

from app.repositories.climate_timeline_snapshot import (
    ClimateProfileConfiguration,
    ClimateScheduleConfiguration,
    ClimateScheduleDraft,
    ClimateScheduleSnapshotBuilder,
    TimelineWindow,
    frozen,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@final
class ReadOnlySchedules:
    def __init__(
        self,
        *,
        active: Mapping[str, object],
        transitions: Mapping[date, Mapping[str, object]] | None = None,
        configurations: Mapping[tuple[int, int | None], ClimateScheduleConfiguration] | None = None,
    ) -> None:
        self._active: Mapping[str, object] = active
        self._transitions: Mapping[date, Mapping[str, object]] = (
            {} if transitions is None else transitions
        )
        self._configurations: Mapping[tuple[int, int | None], ClimateScheduleConfiguration] = (
            {} if configurations is None else configurations
        )

    async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, object]:
        del location, cluster
        return self._active

    async def read_calendar_transition(
        self, location: str, cluster: str, on_date: date
    ) -> Mapping[str, object] | None:
        del location, cluster
        return self._transitions.get(on_date)

    async def read_schedule_configuration(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateScheduleConfiguration | None:
        del location, cluster
        return self._configurations.get((mode_id, submode_id))

    async def write_schedule(self) -> None:
        raise AssertionError("timeline snapshot gathering must never write")


def _configuration(
    mode_id: int, submode_id: int | None, period_id: int
) -> ClimateScheduleConfiguration:
    parameters = {
        "mode_id": mode_id,
        "submode_id": submode_id,
        "day_start_time": "06:00",
        "night_start_time": "18:00",
    }
    periods = (
        {
            "id": period_id,
            "period_name": "Day",
            "start_time": "06:00",
            "end_time": "18:00",
        },
    )
    return ClimateScheduleConfiguration.from_rows(parameters, periods)


@pytest.mark.anyio
async def test_saved_snapshot_resolves_next_calendar_mode_submode_and_period_identity() -> None:
    # Given: an enabled calendar transition with its own saved mode/submode configuration.
    sources = ReadOnlySchedules(
        active={"mode_id": 1, "submode_id": None, "mode_name": "Veg"},
        transitions={
            date(2026, 3, 8): {
                "auto_mode_transition": True,
                "target_mode_id": 2,
                "target_submode_id": 4,
                "target_mode_name": "Flower",
                "target_submode_name": "Bulk",
            }
        },
        configurations={(2, 4): _configuration(2, 4, 44)},
    )

    # When: a daily saved snapshot is gathered for the transition day.
    snapshot = await ClimateScheduleSnapshotBuilder(sources).build_saved(
        "Flower Room", "main", TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    )

    # Then: the exact destination configuration and persisted period identity are retained.
    schedule = snapshot.slices[0].schedule
    assert schedule is not None
    assert schedule.mode["mode_id"] == 2
    assert schedule.mode["submode_id"] == 4
    assert schedule.periods[0]["id"] == 44
    assert schedule.periods[0]["period_name"] == "Day"


@pytest.mark.anyio
async def test_saved_snapshot_keeps_current_mode_when_calendar_transition_is_disabled() -> None:
    # Given: an otherwise valid calendar transition explicitly disabled by its event metadata.
    sources = ReadOnlySchedules(
        active={"mode_id": 1, "submode_id": None, "mode_name": "Veg"},
        transitions={date(2026, 3, 8): {"auto_mode_transition": False, "target_mode_id": 2}},
        configurations={(1, None): _configuration(1, None, 11)},
    )

    # When: the snapshot resolves the transition day.
    snapshot = await ClimateScheduleSnapshotBuilder(sources).build_saved(
        "Flower Room", "main", TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    )

    # Then: the active mode remains authoritative.
    assert snapshot.slices[0].schedule is not None
    assert snapshot.slices[0].schedule.mode["mode_id"] == 1


@pytest.mark.anyio
async def test_saved_snapshot_keeps_flower_active_periods_when_calendar_control_is_disabled() -> (
    None
):
    # Given: Flower has an active schedule with periods while calendar metadata names a missing mode.
    sources = ReadOnlySchedules(
        active={"mode_id": 2, "submode_id": 4, "mode_name": "Flower"},
        transitions={
            date(2026, 3, 8): {
                "calendar_mode_transitions_enabled": False,
                "target_mode_id": 99,
                "target_submode_id": None,
            }
        },
        configurations={(2, 4): _configuration(2, 4, 44)},
    )

    # When: a saved timeline is gathered for the calendar phase day.
    snapshot = await ClimateScheduleSnapshotBuilder(sources).build_saved(
        "Flower Room", "main", TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    )

    # Then: the active Flower configuration supplies a usable timeline instead of an unavailable gap.
    schedule = snapshot.slices[0].schedule
    assert schedule is not None
    assert schedule.mode["mode_id"] == 2
    assert schedule.mode["submode_id"] == 4
    assert schedule.periods[0]["id"] == 44


@pytest.mark.anyio
async def test_saved_snapshot_marks_missing_calendar_mode_configuration_unavailable() -> None:
    # Given: an enabled transition whose destination has no saved configuration.
    sources = ReadOnlySchedules(
        active={"mode_id": 1, "submode_id": None, "mode_name": "Veg"},
        transitions={date(2026, 3, 8): {"auto_mode_transition": True, "target_mode_id": 99}},
    )

    # When: the snapshot resolves the transition day.
    snapshot = await ClimateScheduleSnapshotBuilder(sources).build_saved(
        "Flower Room", "main", TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    )

    # Then: the missing configuration is an explicit unavailable schedule gap.
    assert snapshot.slices[0].schedule is None


def test_daily_window_uses_toronto_midnights_across_dst() -> None:
    # Given: instants on Toronto's spring-forward and fall-back calendar days.
    spring = TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    fall = TimelineWindow.daily(datetime(2026, 11, 1, 12, tzinfo=UTC))

    # When: daily windows are converted to UTC boundaries.
    spring_elapsed = spring.end - spring.start
    fall_elapsed = fall.end - fall.start

    # Then: their local calendar days retain their 23-hour and 25-hour elapsed lengths.
    assert spring_elapsed == timedelta(hours=23)
    assert fall_elapsed == timedelta(hours=25)


def test_rolling_window_is_exactly_twenty_four_elapsed_utc_hours() -> None:
    # Given: an instant during the Toronto spring-forward boundary.
    now = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)

    # When: a rolling window is requested.
    window = TimelineWindow.rolling(now)

    # Then: it advances exactly 24 elapsed hours in UTC.
    assert window.start == now
    assert window.end - window.start == timedelta(hours=24)


@pytest.mark.anyio
async def test_preview_overlays_copied_configuration_without_writing_or_mutating_saved_snapshot() -> (
    None
):
    # Given: a read-only source and a saved configuration that a draft replaces in memory.
    configuration = _configuration(1, None, 11)
    sources = ReadOnlySchedules(
        active={"mode_id": 1, "submode_id": None, "mode_name": "Veg"},
        configurations={(1, None): configuration},
    )
    builder = ClimateScheduleSnapshotBuilder(sources)
    saved = await builder.build_saved(
        "Veg Room", "main", TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    )
    draft = ClimateScheduleDraft.from_rows(
        mode_id=1,
        submode_id=None,
        periods=({"id": 12, "period_name": "Draft", "start_time": "07:00", "end_time": "19:00"},),
        photoperiod={"day_start_time": "07:00", "night_start_time": "19:00"},
    )

    # When: the builder overlays the draft on a copied snapshot.
    preview = builder.preview(saved, draft)

    # Then: saved authority remains unchanged and no source write can have occurred.
    assert saved.slices[0].schedule is not None
    assert saved.slices[0].schedule.periods[0]["id"] == 11
    assert preview.slices[0].schedule is not None
    assert preview.slices[0].schedule.periods[0]["id"] == 12
    assert preview.slices[0].schedule.parameters["day_start_time"] == "07:00"


@pytest.mark.anyio
async def test_profile_snapshot_uses_selected_identity_without_active_or_calendar_reads() -> None:
    # Given: an exact selected Flower/Stretch profile distinct from active Veg authority.
    configuration = _configuration(2, 4, 24)
    frozen_identity = frozen({"mode_id": 2, "submode_id": 4})
    assert frozen_identity is not None
    profile = ClimateProfileConfiguration(
        frozen_identity,
        configuration,
        "0000042",
        True,
    )

    @final
    class ExactProfileOnly:
        async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, object]:
            del location, cluster
            raise AssertionError("profile snapshots must not read active authority")

        async def read_calendar_transition(
            self, location: str, cluster: str, on_date: date
        ) -> Mapping[str, object] | None:
            del location, cluster, on_date
            raise AssertionError("profile snapshots must not read calendar authority")

        async def read_schedule_configuration(
            self, location: str, cluster: str, mode_id: int, submode_id: int | None
        ) -> ClimateScheduleConfiguration | None:
            del location, cluster, mode_id, submode_id
            raise AssertionError("profile snapshots use their exact aggregate read")

        async def read_profile(
            self, location: str, cluster: str, mode_id: int, submode_id: int | None
        ) -> ClimateProfileConfiguration:
            assert (location, cluster, mode_id, submode_id) == ("Flower Room", "main", 2, 4)
            return profile

    # When: the requested Toronto window gathers the selected profile snapshot.
    snapshot = await ClimateScheduleSnapshotBuilder(ExactProfileOnly()).build_profile(
        "Flower Room",
        "main",
        2,
        4,
        TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC)),
    )

    # Then: each slice retains selected identity/revision and no active/calendar authority is read.
    assert snapshot.profile.config_revision == "0000042"
    assert snapshot.profile.mode["mode_id"] == 2
    assert all(
        schedule_slice.schedule is not None
        and schedule_slice.schedule.mode["mode_id"] == 2
        and schedule_slice.schedule.mode["submode_id"] == 4
        for schedule_slice in snapshot.schedule.slices
    )
