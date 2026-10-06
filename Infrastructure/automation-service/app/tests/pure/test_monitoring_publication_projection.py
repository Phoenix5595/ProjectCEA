"""Consumer-level future projection adaptation and calendar identity resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import asyncio

import pytest

from app.repositories.monitoring_snapshot_types import (
    AnchorFingerprint,
    MonitoringSnapshot,
    ProjectionRevision,
    RuntimeSnapshotVersion,
    frozen,
    frozen_rows,
)
from app.repositories.monitoring_snapshot_builder import (
    MonitoringSnapshotBuilder,
    MonitoringSnapshotRepositories,
    MonitoringSnapshotRequest,
)
from app.schemas.monitoring_models import MonitoringRange, Quality
from app.monitoring_publication.projection import (
    ProjectionPublicationError,
    ProjectionPublicationRequest,
    ProjectionPublisher,
)
from app.services.future_projection import (
    project_future_intervals,
    snapshot_publication_version,
    unavailable_future_intervals,
)
from app.repositories.monitoring_snapshot_sources import CalendarSnapshotSource
from shared.monitoring_contracts import (
    ConfigVersion,
    FutureProjection,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality as ProjectionQuality,
    SemanticSeriesId,
)


def _snapshot(
    *,
    location: str = "Flower Room",
    start: datetime = datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
    end: datetime = datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
    active: Mapping[str, object] | None = None,
    events: Sequence[Mapping[str, object]] = (),
    periods: Sequence[Mapping[str, object]] = (),
    expected_lights: Sequence[Mapping[str, object]] = (),
    light_targets: Sequence[Mapping[str, object]] = (),
    config_version: int | None = 1,
    revision: str = "0000001",
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        range=MonitoringRange(start=start, end=end),
        location=location,
        cluster="main",
        active_mode=frozen(active or {"mode_id": 1, "submode_id": None, "mode_name": "veg"}),
        calendar_events=frozen_rows(events),
        calendar_applications=(),
        climate_periods=frozen_rows(periods),
        mode_parameters=None,
        light_targets=frozen_rows(light_targets),
        light_programs=(),
        expected_lights=frozen_rows(expected_lights),
        effective_setpoint_predecessors=(),
        ramp_anchors=(),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(),
        projection_revision=ProjectionRevision(revision),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=end,
        runtime_snapshot_version=RuntimeSnapshotVersion(1),
        config_version=ConfigVersion(config_version) if config_version is not None else None,
    )


def _periods(
    mode_id: int, submode_id: int | None, heating: float, *, ramp: int = 0
) -> tuple[Mapping[str, object], ...]:
    return (
        {
            "id": 10,
            "mode_id": mode_id,
            "submode_id": submode_id,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": heating,
            "ramp_minutes": ramp,
        },
        {
            "id": 11,
            "mode_id": mode_id,
            "submode_id": submode_id,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": heating - 2,
            "ramp_minutes": ramp,
        },
    )


def _heating_values(
    projections: tuple[object, ...],
) -> dict[datetime, float | None]:
    values: dict[datetime, float | None] = {}
    for projection in projections:
        for point in projection.series:
            if point.series_id.value == "climate.heating_setpoint_target":
                values[point.valid_from] = point.value
    return values


def test_future_intervals_scoped_to_exact_active_profile() -> None:
    # Given: active (1, base) heating 22 and inactive (2, base) heating 18 sharing
    # the same clocks; the future must carry 22 only, never the inactive 18.
    periods = (*_periods(1, None, 22), *_periods(2, None, 18))
    projections = project_future_intervals(_snapshot(periods=periods))
    values = _heating_values(projections)
    assert set(values.values()) == {20, 22}
    assert 18 not in values.values()


def test_calendar_destination_future_projects_its_exact_profile() -> None:
    # Given: a configured calendar destination (2, base) stored heating 18 for the
    # second local day; its exact profile is projectable from stored authority.
    periods = (*_periods(1, None, 22), *_periods(2, None, 18))
    events = [
        {
            "id": 5,
            "start_date": date(2026, 6, 2),
            "end_date": date(2026, 6, 2),
            "phase_order": 4,
            "target_mode_id": 2,
            "target_submode_id": None,
            "auto_mode_transition": True,
            "destination_configured": True,
        }
    ]
    projections = project_future_intervals(
        _snapshot(
            periods=periods,
            events=events,
            end=datetime(2026, 6, 2, 15, 0, tzinfo=UTC),
        )
    )
    values = _heating_values(projections)
    # The destination identity governs from the 06-02 local midnight onward: its
    # stored night profile (16) applies until its day profile (18) starts.
    assert values[datetime(2026, 6, 1, 10, 0, tzinfo=UTC)] == 22
    assert values[datetime(2026, 6, 1, 22, 0, tzinfo=UTC)] == 20
    assert values[datetime(2026, 6, 2, 4, 0, tzinfo=UTC)] == 16
    assert values[datetime(2026, 6, 2, 10, 0, tzinfo=UTC)] == 18


def test_period_gap_future_interval_is_unavailable_not_held() -> None:
    periods = (
        {
            "id": 10,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "12:00",
            "heating_setpoint": 20,
            "ramp_minutes": 0,
        },
        {
            "id": 11,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    )
    projections = project_future_intervals(_snapshot(periods=periods))
    values = _heating_values(projections)
    gap_start = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
    assert values[gap_start] is None
    assert values[datetime(2026, 6, 1, 10, 0, tzinfo=UTC)] == 20
    assert values[datetime(2026, 6, 1, 22, 0, tzinfo=UTC)] == 18


def test_interval_cap_returns_honest_unavailable() -> None:
    # Given: long ramps generating more minute boundaries than the interval cap;
    # the whole timeline becomes one honest unavailable tuple, never a truncation.
    periods = (*_periods(1, None, 22, ramp=200), *_periods(2, None, 18, ramp=200))
    snapshot = _snapshot(periods=periods)
    projections = project_future_intervals(snapshot)
    assert len(projections) == 1
    projection = projections[0]
    assert projection.valid_from == snapshot.range.start
    assert projection.valid_until == snapshot.range.end
    assert all(point.value is None for point in projection.series)
    assert all(point.quality is ProjectionQuality.UNAVAILABLE for point in projection.series)


def test_unavailable_future_intervals_is_exported() -> None:
    snapshot = _snapshot()
    version = snapshot_publication_version(snapshot)
    assert version is not None
    projections = unavailable_future_intervals(snapshot, version)
    assert len(projections) == 1
    projection = projections[0]
    assert projection.version == version
    assert projection.valid_from == snapshot.range.start
    assert projection.valid_until == snapshot.range.end
    assert all(point.value is None for point in projection.series)


def test_no_config_version_yields_no_future() -> None:
    assert project_future_intervals(_snapshot(config_version=None)) == ()


_PUBLISHER_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def version(config_version: int) -> PublicationVersion:
    """The proven original publisher fixture version on one shared revision."""
    return PublicationVersion(
        contract_version=1,
        config_version=ConfigVersion(config_version),
        revision=ProjectionRevision("8f8c3db"),
    )


def projection(
    config_version: int, valid_until: datetime, valid_from: datetime = _PUBLISHER_NOW
) -> FutureProjection:
    return FutureProjection(
        version=version(config_version),
        generated_at=_PUBLISHER_NOW,
        valid_from=valid_from,
        valid_until=valid_until,
        series=(
            ProjectionSeriesPoint(
                series_id=SemanticSeriesId(value="climate.heating_setpoint_target"),
                value=21.0,
                quality=ProjectionQuality.ESTIMATED,
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        ),
    )


def request(config_version: int = 7) -> ProjectionPublicationRequest:
    return ProjectionPublicationRequest(
        location="Veg Room", version=version(config_version), observed_at=_PUBLISHER_NOW
    )


class RecordingStore:
    """Atomic cache boundary recording every replacement attempt."""

    def __init__(self, cached: FutureProjection | None = None) -> None:
        self.cached = cached
        self.replaced: list[FutureProjection] = []

    def read_projection(self, location: str) -> FutureProjection | None:
        del location
        return self.cached

    def replace_projection(self, location: str, projection: FutureProjection) -> None:
        self.replaced.append(projection)
        self.cached = projection


class StubFactory:
    """Factory stub recording the requests it satisfied."""

    def __init__(self, projection: FutureProjection) -> None:
        self.projection = projection
        self.calls: list[ProjectionPublicationRequest] = []

    def build(self, request: ProjectionPublicationRequest) -> FutureProjection:
        self.calls.append(request)
        return self.projection


def test_cache_miss_builds_and_atomically_replaces_the_projection() -> None:
    # Given: no cached projection and a factory holding the matching-version facts.
    store = RecordingStore(cached=None)
    built = projection(7, _PUBLISHER_NOW + timedelta(hours=1))
    factory = StubFactory(built)

    # When: a cache miss reaches the background publisher.
    result = ProjectionPublisher(store, factory).publish_if_stale(request())

    # Then: the built projection is returned as one atomic replacement.
    assert result is built
    assert factory.calls == [request()]
    assert store.replaced == [built]


def test_factory_result_expiring_at_or_before_the_request_is_rejected() -> None:
    # Given: a factory fact whose validity ends at the observed request instant.
    store = RecordingStore(cached=None)
    factory = StubFactory(projection(7, _PUBLISHER_NOW))

    # When: the publisher evaluates the requested revision.
    with pytest.raises(ProjectionPublicationError):
        ProjectionPublisher(store, factory).publish_if_stale(request())

    # Then: an at-expiry payload cannot replace the cached authority.
    assert store.replaced == []


def test_future_light_intensity_unavailable_without_targets() -> None:
    snapshot = _snapshot(expected_lights=[{"device_id": 7, "device_name": "light_1"}])
    projections = project_future_intervals(snapshot)
    light_values = {
        point.valid_from: (point.value, point.quality)
        for projection in projections
        for point in projection.series
        if point.series_id.value == "light.intensity.light_1"
    }
    assert light_values
    assert all(
        value is None and quality is ProjectionQuality.UNAVAILABLE
        for value, quality in light_values.values()
    )


def test_minute_boundaries_within_scheduled_ramp_are_sampled() -> None:
    periods = _periods(1, None, 24, ramp=30)
    projections = project_future_intervals(_snapshot(periods=periods))
    heating = _heating_values(projections)
    # The Day ramp starts at 06:00 Toronto (10:00Z) from the night value 22;
    # interior one-minute ticks approximate the ramp between shared boundaries.
    assert len(heating) > 3
    first_start = min(heating)
    assert heating[first_start] == 22
    later = heating[datetime(2026, 6, 1, 10, 15, tzinfo=UTC)]
    assert later == 23


@dataclass
class _FakeCalendarRepo:
    events: tuple[Mapping[str, object], ...]
    calls: list[tuple[date, date, str | None]] = field(default_factory=list)

    async def list_events(
        self,
        from_date: date,
        to_date: date,
        location: str | None = None,
        limit: int = 500,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, object]], str | None]:
        self.calls.append((from_date, to_date, location))
        rows = [
            dict(event)
            for event in self.events
            if event.get("location") == location
            and event.get("start_date", date.min) <= to_date
            and (event.get("end_date") or event.get("start_date", date.min)) >= from_date
        ]
        return rows, None

    @staticmethod
    def parse_metadata(meta: object) -> dict[str, object]:
        return dict(meta) if isinstance(meta, Mapping) else {}


class _FakeScheduler:
    """Read-only scheduler fake on the declared public resolution boundary."""

    def __init__(
        self,
        transitions: Mapping[date, Mapping[str, object] | None],
        profile_configured: bool = True,
    ) -> None:
        self.transitions = transitions
        self.profile_configured = profile_configured
        self.dates: list[date] = []
        self.configured_calls: list[tuple[str, str, str, str | None]] = []

    async def get_expected_transition(
        self, location: str, cluster: str, on_date: date
    ) -> Mapping[str, object] | None:
        self.dates.append(on_date)
        return self.transitions.get(on_date)

    async def is_profile_configured(
        self, location: str, cluster: str, mode_name: str, submode_name: str | None
    ) -> bool:
        self.configured_calls.append((location, cluster, mode_name, submode_name))
        return self.profile_configured


def test_calendar_source_resolves_real_destination_identity() -> None:
    # Given: production-style raw rows whose targets live in metadata, and the
    # read-only scheduler expectation source resolving IDs per Toronto date.
    event = {
        "id": 5,
        "location": "Flower Room",
        "cluster": "main",
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 6, 2),
        "event_type": "phase",
        "title": "Stretch",
        "metadata": {
            "auto_mode_transition": True,
            "phase_order": 4,
            "target_mode_name": "flower",
            "target_submode_name": "stretch",
        },
    }
    sibling = {
        "id": 9,
        "location": "Flower Room",
        "cluster": "moon",
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 6, 2),
        "event_type": "phase",
        "title": "Other",
        "metadata": {},
    }
    transition = {
        "event_id": 5,
        "auto_mode_transition": True,
        "calendar_mode_transitions_enabled": True,
        "target_mode_name": "flower",
        "target_submode_name": "stretch",
        "target_mode_id": 1,
        "target_submode_id": 2,
    }
    repo = _FakeCalendarRepo(events=(event, sibling))
    scheduler = _FakeScheduler(
        transitions={
            date(2026, 5, 31): None,
            date(2026, 6, 1): transition,
            date(2026, 6, 2): transition,
        },
    )
    source = CalendarSnapshotSource(repo, scheduler)
    rows = asyncio.run(
        source.read_calendar_events(
            "Flower Room",
            "main",
            datetime(2026, 6, 1, 3, 30, tzinfo=UTC),
            datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
        )
    )
    assert [row["id"] for row in rows] == [5]
    row = rows[0]
    assert row["target_mode_id"] == 1
    assert row["target_submode_id"] == 2
    assert row["auto_mode_transition"] is True
    assert row["phase_order"] == 4
    assert row["target_mode_name"] == "flower"
    assert row["target_submode_name"] == "stretch"
    assert row["destination_configured"] is True
    # Activation authority is the declared public configured-profile boundary.
    assert scheduler.configured_calls == [("Flower Room", "main", "flower", "stretch")]
    # The fetched bounds and resolution dates are Toronto local dates.
    assert repo.calls == [(date(2026, 5, 31), date(2026, 6, 2), "Flower Room")]
    assert scheduler.dates == [date(2026, 5, 31), date(2026, 6, 1), date(2026, 6, 2)]


def test_calendar_source_unconfigured_destination_is_not_activation_authority() -> None:
    # Given: a resolved destination without persisted mode parameters; the row
    # reports it unconfigured so the canonical projection never paints it.
    event = {
        "id": 5,
        "location": "Flower Room",
        "cluster": "main",
        "start_date": date(2026, 6, 1),
        "end_date": None,
        "event_type": "phase",
        "title": "Stretch",
        "metadata": {
            "auto_mode_transition": True,
            "phase_order": 4,
            "target_mode_name": "flower",
            "target_submode_name": None,
        },
    }
    transition = {
        "event_id": 5,
        "auto_mode_transition": True,
        "calendar_mode_transitions_enabled": True,
        "target_mode_name": "flower",
        "target_submode_name": None,
        "target_mode_id": 2,
        "target_submode_id": None,
    }
    repo = _FakeCalendarRepo(events=(event,))
    scheduler = _FakeScheduler(
        transitions={date(2026, 6, 1): transition, date(2026, 6, 2): transition},
        profile_configured=False,
    )
    source = CalendarSnapshotSource(repo, scheduler)
    rows = asyncio.run(
        source.read_calendar_events(
            "Flower Room",
            "main",
            datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
        )
    )
    row = rows[0]
    assert row["target_mode_id"] == 2
    assert row["destination_configured"] is False
    assert scheduler.configured_calls == [("Flower Room", "main", "flower", None)]


def test_calendar_source_keeps_unresolved_destination_unresolved() -> None:
    event = {
        "id": 6,
        "location": "Flower Room",
        "cluster": "main",
        "start_date": date(2026, 6, 1),
        "end_date": None,
        "event_type": "phase",
        "title": "Unknown target",
        "metadata": {"auto_mode_transition": False, "target_mode_name": "ghost"},
    }
    repo = _FakeCalendarRepo(events=(event,))
    scheduler = _FakeScheduler(transitions={})
    source = CalendarSnapshotSource(repo, scheduler)
    rows = asyncio.run(
        source.read_calendar_events(
            "Flower Room",
            "main",
            datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
        )
    )
    row = rows[0]
    assert "target_mode_id" not in row
    assert "target_submode_id" not in row
    assert row["auto_mode_transition"] is False
    assert row["target_mode_name"] == "ghost"


def test_snapshot_builder_builds_enriched_calendar_events() -> None:
    class _ModeRepo:
        async def read_active_mode(
            self, location: str, cluster: str
        ) -> Mapping[str, object] | None:
            return {"mode_id": 1, "submode_id": None, "mode_name": "flower"}

        async def read_mode_parameters(
            self, location: str, cluster: str, active_mode: Mapping[str, object] | None
        ) -> Mapping[str, object] | None:
            return None

    class _CalendarRepo(_FakeCalendarRepo):
        async def flower_calendar_mode_transitions_enabled(self) -> bool:
            return True

        async def get_active_flower_phase_event(self, on_date: date) -> dict[str, object] | None:
            return None

    class _NoSource:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __getattr__(self, name: str):
            async def _missing(*args: object, **kwargs: object):
                return ()

            return _missing

    class _TransitionScheduler:
        def __init__(self) -> None:
            self.transitions: dict[date, Mapping[str, object]] = {}

        async def get_expected_transition(
            self, location: str, cluster: str, on_date: date
        ) -> Mapping[str, object] | None:
            return {
                "event_id": 5,
                "auto_mode_transition": True,
                "calendar_mode_transitions_enabled": True,
                "target_mode_name": "flower",
                "target_submode_name": "stretch",
                "target_mode_id": 1,
                "target_submode_id": 2,
            }

        async def is_profile_configured(
            self, location: str, cluster: str, mode_name: str, submode_name: str | None
        ) -> bool:
            return True

        async def get_expected_mode(
            self, location: str, cluster: str, on_date: date
        ) -> dict[str, object]:
            return {"mode_name": None, "submode_name": None}

    event = {
        "id": 5,
        "location": "Flower Room",
        "cluster": "main",
        "start_date": date(2026, 6, 1),
        "end_date": date(2026, 6, 2),
        "event_type": "phase",
        "title": "Stretch",
        "metadata": {
            "auto_mode_transition": True,
            "phase_order": 4,
            "target_mode_name": "flower",
            "target_submode_name": "stretch",
        },
    }
    calendar_repo = _CalendarRepo(events=(event,))
    builder = MonitoringSnapshotBuilder(
        MonitoringSnapshotRepositories(
            modes=_ModeRepo(),
            calendar=CalendarSnapshotSource(calendar_repo, _TransitionScheduler()),
            climate=_NoSource(),
            lights=_NoSource(),
            anchors=_NoSource(),
            versions=_NoSource(),
        )
    )
    snapshot = asyncio.run(
        builder.build(
            MonitoringSnapshotRequest(
                location="Flower Room",
                cluster="main",
                now=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
                runtime_snapshot_version=RuntimeSnapshotVersion(1),
            )
        )
    )
    rows = [dict(row) for row in snapshot.calendar_events]
    assert (
        rows
        and rows[0]["target_mode_id"] == 1
        and rows[0]["target_submode_id"] == 2
        and rows[0]["destination_configured"] is True
    )
    assert rows[0]["phase_order"] == 4
