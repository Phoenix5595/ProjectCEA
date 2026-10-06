"""Consumer-level ProjectionPublicationAction overlay, conflict, and cache behavior."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
import asyncio
import time

import pytest

from app.monitoring_publication.projection import (
    ProjectionPublicationAction,
    ProjectionPublicationDependencies,
)
from app.repositories.monitoring_snapshot_builder import MonitoringSnapshotRequest
from app.repositories.monitoring_snapshot_types import (
    AnchorFingerprint,
    MonitoringSnapshot,
    ProjectionRevision,
    RuntimeSnapshotVersion,
    frozen,
    frozen_rows,
)
from app.schemas.monitoring_models import MonitoringRange, Quality
from app.schemas.monitoring_models import ProjectionRevision as SourceProjectionRevision
from app.schemas.monitoring_models import Quality as SourceQuality
from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    FutureProjection,
    PersistenceCursor,
    PersistenceState,
    ProjectionRevision as ContractRevision,
    PublicationVersion,
    ProjectionSeriesPoint,
    Quality as ContractQuality,
    SemanticSeriesId,
)

_PREFIX = "flower_room.main.setpoint"
_NOW = datetime(2026, 6, 1, 10, 15, tzinfo=UTC)


def _version(config: int = 1, revision: str = "0000001") -> PublicationVersion:
    return PublicationVersion(
        contract_version=1,
        config_version=ConfigVersion(config),
        revision=ContractRevision(revision),
    )


def _fact_current(
    *,
    observed_at: datetime = _NOW,
    valid_until: datetime | None = None,
    mode_id: int | None = 1,
    submode_id: int | None = None,
    nominal: float | None = 24.0,
    effective: float | None = 21.0,
    remaining: float | None = 900.0,
    config: int = 1,
    revision: str = "0000001",
) -> CurrentSnapshot:
    series: list[CurrentSeriesPoint] = []

    def add(name: str, value: float | int | None) -> None:
        if value is None:
            return
        series.append(
            CurrentSeriesPoint(
                series_id=SemanticSeriesId(value=f"{_PREFIX}.{name}"),
                value=float(value),
                quality=ContractQuality.EXACT,
                observed_at=observed_at,
                valid_until=valid_until or observed_at + timedelta(seconds=5),
            )
        )

    if mode_id is not None:
        add("profile_mode_id", mode_id)
    if submode_id is not None:
        add("profile_submode_id", submode_id)
    add("nominal_heating_setpoint", nominal)
    add("effective_heating_setpoint", effective)
    add("ramp_remaining_seconds_heating", remaining)
    return CurrentSnapshot(
        version=_version(config, revision),
        observed_at=observed_at,
        valid_until=valid_until or observed_at + timedelta(seconds=5),
        series=tuple(series),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def _snapshot(
    *,
    start: datetime = _NOW,
    end: datetime | None = None,
    active: Mapping[str, object] | None = None,
    periods: tuple[Mapping[str, Any], ...] | None = None,
    config: int = 1,
    revision: str = "0000001",
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        range=MonitoringRange(start=start, end=end or start + timedelta(hours=24)),
        location="Flower Room",
        cluster="main",
        active_mode=frozen(
            active if active is not None else {"mode_id": 1, "submode_id": None, "mode_name": "veg"}
        ),
        calendar_events=(),
        calendar_applications=(),
        climate_periods=frozen_rows(
            periods
            if periods is not None
            else (
                {
                    "id": 10,
                    "mode_id": 1,
                    "submode_id": None,
                    "period_name": "day",
                    "start_time": "06:00",
                    "end_time": "18:00",
                    "heating_setpoint": 24,
                    "ramp_minutes": 30,
                },
                {
                    "id": 11,
                    "mode_id": 1,
                    "submode_id": None,
                    "period_name": "night",
                    "start_time": "18:00",
                    "end_time": "06:00",
                    "heating_setpoint": 20,
                    "ramp_minutes": 30,
                },
            )
        ),
        mode_parameters=None,
        light_targets=(),
        light_programs=(),
        expected_lights=(),
        effective_setpoint_predecessors=(),
        ramp_anchors=(),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(),
        projection_revision=ProjectionRevision(revision),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=end or start + timedelta(hours=24),
        runtime_snapshot_version=RuntimeSnapshotVersion(1),
        config_version=ConfigVersion(config),
    )


@dataclass
class _Builder:
    snapshot: MonitoringSnapshot
    builds: list[object] = field(default_factory=list)
    on_build: Callable[[], None] | None = None

    async def build(self, request: object) -> MonitoringSnapshot:
        self.builds.append(request)
        if self.on_build is not None:
            self.on_build()
        return self.snapshot


@dataclass
class _Writer:
    ok: bool = True
    futures: list[tuple[str, tuple[FutureProjection, ...]]] = field(default_factory=list)

    def write_future(self, location: str, projections: tuple[FutureProjection, ...]) -> bool:
        if not self.ok:
            return False
        self.futures.append((location, projections))
        return True


def _action(
    *,
    builder: _Builder,
    current: CurrentSnapshot | None,
    writer: _Writer,
    now: datetime = _NOW,
) -> tuple[ProjectionPublicationAction, dict[str, Any]]:
    state: dict[str, Any] = {"current": current, "now": now}

    def read_fact_current() -> CurrentSnapshot | None:
        return state["current"]

    def clock() -> datetime:
        return state["now"]

    action = ProjectionPublicationAction(
        "Flower Room",
        "main",
        ProjectionPublicationDependencies(
            snapshot_builder=builder,
            current_snapshot=read_fact_current,
            writer=writer,
            now=clock,
            rich_config_revision=None,
        ),
    )
    return action, state


def _heating_values(projections: tuple[FutureProjection, ...]) -> dict[datetime, float | None]:
    values: dict[datetime, float | None] = {}
    for projection in projections:
        for point in projection.series:
            if point.series_id.value == "climate.heating_setpoint_target":
                values[point.valid_from] = point.value
    return values


def test_compatible_live_ramp_governs_executing_period() -> None:
    # Given: fresh facts matching the DB authority (nominal 24 == stored 24) with
    # an in-flight ramp 21→24 over the remaining 15 minutes.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, _ = _action(builder=builder, current=_fact_current(), writer=writer)
    assert asyncio.run(action.publish()) is True
    values = _heating_values(writer.futures[0][1])
    first_start = min(values)
    assert values[first_start] == 21
    assert values[first_start + timedelta(minutes=15)] == 24
    # The night ramp starts from the day value and reaches its target 30 minutes in.
    assert values[datetime(2026, 6, 1, 22, 0, tzinfo=UTC)] == 24
    assert values[datetime(2026, 6, 1, 22, 30, tzinfo=UTC)] == 20


def test_countdown_stable_signature_keeps_cached_projection() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(builder=builder, current=_fact_current(), writer=writer)
    assert asyncio.run(action.publish()) is True
    assert len(builder.builds) == 1
    # Two seconds pass; remaining counts down but the ramp end epoch is unchanged.
    state["now"] = _NOW + timedelta(seconds=2)
    state["current"] = _fact_current(observed_at=_NOW + timedelta(seconds=2), remaining=898.0)
    assert asyncio.run(action.publish()) is None
    assert len(builder.builds) == 1
    assert len(writer.futures) == 1


def test_racing_compatible_tick_does_not_abort_publication() -> None:
    # Given: a new tick lands during the build whose only change is the
    # observation countdown over a compatible anchor signature; the valid
    # same-signature forecast must still publish.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(builder=builder, current=_fact_current(), writer=writer)
    builder.on_build = lambda: state.update(
        {"current": _fact_current(observed_at=_NOW + timedelta(seconds=2), remaining=898.0)}
    )
    assert asyncio.run(action.publish()) is True
    assert len(builder.builds) == 1
    assert writer.futures


def test_racing_profile_change_aborts_publication() -> None:
    # Given: a tick during the build whose profile identity changed; the built
    # forecast must not publish, store, or relabel the old profile.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(builder=builder, current=_fact_current(), writer=writer)
    builder.on_build = lambda: state.update(
        {"current": _fact_current(mode_id=2, nominal=18.0, effective=18.0, remaining=0.0)}
    )
    assert asyncio.run(action.publish()) is None
    assert writer.futures == []
    assert action.last_good is None


def test_racing_ramp_end_change_aborts_publication() -> None:
    # Given: a tick during the build whose ramp end epoch changed; the built
    # forecast embeds a stale anchor end and must not publish.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(builder=builder, current=_fact_current(), writer=writer)
    builder.on_build = lambda: state.update(
        {"current": _fact_current(observed_at=_NOW + timedelta(seconds=2), remaining=600.0)}
    )
    assert asyncio.run(action.publish()) is None
    assert writer.futures == []


def test_ramp_end_change_rebuilds_without_version_change() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(builder=builder, current=_fact_current(), writer=writer)
    assert asyncio.run(action.publish()) is True
    # The ramp ends: remaining zero flips the active flag with the same version.
    state["current"] = _fact_current(effective=24.0, remaining=0.0)
    assert asyncio.run(action.publish()) is True
    assert len(builder.builds) == 2
    values = _heating_values(writer.futures[-1][1])
    first_start = min(values)
    # The no-ramp executing period holds the actual nominal, no invented ramp.
    assert values[first_start] == 24


def test_signature_stored_only_after_successful_publish() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer(ok=False)
    action, _ = _action(builder=builder, current=_fact_current(), writer=writer)
    assert asyncio.run(action.publish()) is False
    assert action.last_good is None
    assert action.last_good_signature is None
    writer.ok = True
    assert asyncio.run(action.publish()) is True
    assert len(builder.builds) == 2
    assert action.last_good_signature is not None


def test_stale_current_facts_do_not_overlay_or_conflict() -> None:
    # Given: expired current facts whose nominal disagrees with the DB target;
    # stale facts assert nothing, so the configured projection stays available.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    current = _fact_current(
        observed_at=_NOW - timedelta(seconds=10),
        valid_until=_NOW - timedelta(seconds=5),
        nominal=99.0,
    )
    action, _ = _action(builder=builder, current=current, writer=writer)
    assert asyncio.run(action.publish()) is True
    values = _heating_values(writer.futures[0][1])
    first_start = min(values)
    assert values[first_start] == 22


def test_profile_mismatch_publishes_unavailable_future() -> None:
    # Given: fresh facts for another profile than the DB-active row; the future
    # tuple is honestly unavailable while actual current facts remain untouched.
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, _ = _action(
        builder=builder,
        current=_fact_current(mode_id=2, nominal=18.0, effective=18.0, remaining=0.0),
        writer=writer,
    )
    assert asyncio.run(action.publish()) is True
    projections = writer.futures[0][1]
    assert len(projections) == 1
    assert all(point.value is None for point in projections[0].series)
    assert all(point.quality is ContractQuality.UNAVAILABLE for point in projections[0].series)


def test_nominal_disagreement_publishes_unavailable_future() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, _ = _action(
        builder=builder,
        current=_fact_current(nominal=22.0, effective=22.0, remaining=0.0),
        writer=writer,
    )
    assert asyncio.run(action.publish()) is True
    projections = writer.futures[0][1]
    assert len(projections) == 1
    assert all(point.value is None for point in projections[0].series)


def test_recovered_compatibility_rebuilds_after_mismatch() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, state = _action(
        builder=builder,
        current=_fact_current(mode_id=2, nominal=18.0, remaining=0.0),
        writer=writer,
    )
    assert asyncio.run(action.publish()) is True
    state["current"] = _fact_current()
    assert asyncio.run(action.publish()) is True
    assert len(builder.builds) == 2
    values = _heating_values(writer.futures[-1][1])
    first_start = min(values)
    assert values[first_start] == 21


def test_missing_current_returns_none() -> None:
    builder = _Builder(snapshot=_snapshot())
    writer = _Writer()
    action, _ = _action(builder=builder, current=None, writer=writer)
    assert asyncio.run(action.publish()) is None
    assert builder.builds == []


# ---------------------------------------------------------------------------
# Restored original consumer coverage (pytest-8.3.5 retained bytecode,
# adapted to the current projector arity; consumer outcomes unchanged).
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _current(version: int = 7) -> CurrentSnapshot:
    publication_version = PublicationVersion(
        contract_version=1,
        config_version=ConfigVersion(version),
        revision=ContractRevision("000000c"),
    )
    return CurrentSnapshot(
        version=publication_version,
        observed_at=NOW,
        valid_until=NOW + timedelta(seconds=5),
        series=(
            CurrentSeriesPoint(
                series_id=SemanticSeriesId(value="climate.air_temperature_setpoint"),
                value=22.0,
                quality=ContractQuality.EXACT,
                observed_at=NOW,
                valid_until=NOW + timedelta(seconds=5),
            ),
        ),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def _future(version: int = 7) -> tuple[FutureProjection, ...]:
    publication_version = _current(version).version
    return (
        FutureProjection(
            version=publication_version,
            generated_at=NOW,
            valid_from=NOW,
            valid_until=NOW + timedelta(hours=1),
            series=(
                ProjectionSeriesPoint(
                    series_id=SemanticSeriesId(value="climate.heating_setpoint_target"),
                    value=21.0,
                    quality=ContractQuality.ESTIMATED,
                    valid_from=NOW,
                    valid_until=NOW + timedelta(hours=1),
                ),
            ),
        ),
    )


@dataclass
class FakeBuilder:
    """Snapshot builder fake recording the runtime snapshot versions requested."""

    requests: list[RuntimeSnapshotVersion]

    async def build(self, request: MonitoringSnapshotRequest) -> MonitoringSnapshot:
        self.requests.append(request.runtime_snapshot_version)
        return MonitoringSnapshot(
            range=MonitoringRange.from_absolute(NOW, NOW + timedelta(minutes=5)),
            location="Veg Room",
            cluster="main",
            active_mode=None,
            calendar_events=(),
            calendar_applications=(),
            climate_periods=(),
            mode_parameters=None,
            light_targets=(),
            light_programs=(),
            expected_lights=(),
            effective_setpoint_predecessors=(),
            ramp_anchors=(),
            automation_state_predecessors=(),
            photoperiod_predecessor=None,
            source_cursors=(("configuration", 7),),
            projection_revision=SourceProjectionRevision("000000c"),
            anchor_fingerprint=AnchorFingerprint("test"),
            anchor_observed_at=NOW,
            anchor_quality=SourceQuality.EXACT,
            anchor_valid_until=NOW + timedelta(minutes=5),
            runtime_snapshot_version=request.runtime_snapshot_version,
            config_version=ConfigVersion(7),
        )


@dataclass
class FakeWriter:
    """Writer fake consuming one outcome per write attempt."""

    outcomes: list[bool]
    payloads: list[tuple[FutureProjection, ...]]

    def write_future(self, location: str, projections: tuple[FutureProjection, ...]) -> bool:
        assert location == "Veg Room"
        self.payloads.append(projections)
        return self.outcomes.pop(0)


class SlowWriter:
    """Writer fake that exceeds any small Redis timeout."""

    def write_future(self, location: str, projections: tuple[FutureProjection, ...]) -> bool:
        del location, projections
        time.sleep(0.05)
        return True


@pytest.mark.anyio
async def test_projection_action_rebuilds_on_version_and_retains_last_good_after_failure() -> None:
    current = _current()
    builder = FakeBuilder([])
    writer = FakeWriter([True, False], [])
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        ProjectionPublicationDependencies(
            snapshot_builder=builder,
            current_snapshot=lambda: current,
            writer=writer,
            projector=lambda snapshot: _future(int(current.version.config_version)),
            now=lambda: NOW,
        ),
    )
    # A configuration version trigger rebuilds and stores the last good timeline.
    assert await action.publish() is True
    current = _current(8)
    # A failed write is reported while the previously published timeline is retained.
    assert await action.publish() is False
    assert len(writer.payloads) == 2
    assert action.last_good == _future()
    assert builder.requests == [RuntimeSnapshotVersion(12), RuntimeSnapshotVersion(12)]


@pytest.mark.anyio
async def test_projection_action_rebuilds_after_cached_timeline_expiry() -> None:
    now = NOW
    builder = FakeBuilder([])
    writer = FakeWriter([True, True], [])
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        ProjectionPublicationDependencies(
            snapshot_builder=builder,
            current_snapshot=_current,
            writer=writer,
            projector=lambda snapshot: _future(),
            now=lambda: now,
        ),
    )
    # The first refresh builds and stores a complete retained timeline.
    assert await action.publish() is True
    now = NOW + timedelta(hours=2)
    # After the cached timeline expires, the next refresh rebuilds and republishes.
    assert await action.publish() is True
    assert len(writer.payloads) == 2
    assert builder.requests == [RuntimeSnapshotVersion(12), RuntimeSnapshotVersion(12)]


@pytest.mark.anyio
async def test_projection_action_refuses_mixed_current_and_future_versions_before_redis() -> None:
    writer = FakeWriter([True], [])
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        ProjectionPublicationDependencies(
            snapshot_builder=FakeBuilder([]),
            current_snapshot=_current,
            writer=writer,
            projector=lambda snapshot: _future(8),
            now=lambda: NOW,
        ),
    )
    # Mixed current/future versions are refused before any Redis handoff.
    assert await action.publish() is False
    assert writer.payloads == []


@pytest.mark.anyio
async def test_projection_action_timeout_retains_no_new_cache() -> None:
    action = ProjectionPublicationAction(
        "Veg Room",
        "main",
        ProjectionPublicationDependencies(
            snapshot_builder=FakeBuilder([]),
            current_snapshot=_current,
            writer=SlowWriter(),
            projector=lambda snapshot: _future(),
            now=lambda: NOW,
            redis_timeout_seconds=0.001,
        ),
    )
    # A Redis timeout publishes nothing and stores no new cache.
    assert await action.publish() is False
    assert action.last_good is None
