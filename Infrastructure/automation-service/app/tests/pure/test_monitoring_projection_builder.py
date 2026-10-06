from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.monitoring_snapshot_builder import (
    MonitoringSnapshotBuilder,
    MonitoringSnapshotRepositories,
    MonitoringSnapshotRequest,
)
from app.schemas.monitoring_models import RuntimeSnapshotVersion
from app.services.future_projection import MAX_PROJECTION_INTERVALS, project_future_intervals
from shared.monitoring_contracts import Quality, validate_projection_timeline

NOW = datetime(2026, 3, 8, 5, 30, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class FakeSources:
    active_mode: Mapping[str, object] | None = None
    mode_parameters: Mapping[str, object] | None = None
    climate_periods: Sequence[Mapping[str, object]] = ()
    light_targets: Sequence[Mapping[str, object]] = ()
    source_versions: tuple[tuple[str, int | None], ...] = (("configuration", 7),)

    async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, object] | None:
        del location, cluster
        return self.active_mode

    async def read_mode_parameters(
        self, location: str, cluster: str, active_mode: Mapping[str, object] | None
    ) -> Mapping[str, object] | None:
        del location, cluster, active_mode
        return self.mode_parameters

    async def read_calendar_events(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start, end
        return ()

    async def read_calendar_applications(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start, end
        return ()

    async def read_climate_periods(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster
        return self.climate_periods

    async def read_light_targets(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster
        return self.light_targets

    async def read_light_programs(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start, end
        return ()

    async def read_expected_lights(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster
        return ({"device_id": 7, "device_name": "light_1"},)

    async def read_effective_setpoint_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start
        return ()

    async def read_ramp_anchors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start
        return ()

    async def read_automation_state_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]:
        del location, cluster, start
        return ()

    async def read_photoperiod_predecessor(
        self, location: str, cluster: str, start: datetime
    ) -> Mapping[str, object] | None:
        del location, cluster, start
        return None

    async def read_source_versions(
        self, location: str, cluster: str
    ) -> tuple[tuple[str, int | None], ...]:
        del location, cluster
        return self.source_versions


def _repositories(fake: FakeSources) -> MonitoringSnapshotRepositories:
    return MonitoringSnapshotRepositories(
        modes=fake,
        calendar=fake,
        climate=fake,
        lights=fake,
        anchors=fake,
        versions=fake,
    )


@pytest.mark.anyio
async def test_builder_collects_fake_authority_into_exact_twenty_four_hour_snapshot() -> None:
    # Given: fake repositories carrying every configured schedule authority.
    fake = FakeSources(
        active_mode={"mode_id": 1, "mode_name": "Veg"},
        mode_parameters={
            "mode_id": 1,
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "light_ramp_up_minutes": 30,
            "light_ramp_down_minutes": 30,
        },
        climate_periods=(
            {
                "start_time": "06:00",
                "heating_setpoint": 20,
                "cooling_setpoint": 25,
                "vpd_setpoint": 1,
                "co2_setpoint": 800,
            },
        ),
        light_targets=({"device_id": 7, "target_intensity": 60},),
    )

    # When: the asynchronous read-only builder creates its input snapshot.
    snapshot = await MonitoringSnapshotBuilder(_repositories(fake)).build(
        MonitoringSnapshotRequest(
            location="Veg Room",
            cluster="main",
            now=NOW,
            runtime_snapshot_version=RuntimeSnapshotVersion(12),
        )
    )

    # Then: it preserves a normalized, half-open 24-hour authority window.
    assert snapshot.range.start == NOW
    assert snapshot.range.end - snapshot.range.start == timedelta(hours=24)
    assert snapshot.source_cursors == (("configuration", 7),)
    assert snapshot.config_version == 7


@pytest.mark.anyio
async def test_projection_intervals_cover_snapshot_without_device_or_pid_series() -> None:
    # Given: a complete Veg schedule across Toronto's spring DST boundary.
    fake = FakeSources(
        active_mode={"mode_id": 1, "mode_name": "Veg"},
        mode_parameters={
            "mode_id": 1,
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "light_ramp_up_minutes": 60,
            "light_ramp_down_minutes": 60,
        },
        climate_periods=(
            {
                "start_time": "06:00",
                "heating_setpoint": 20,
                "cooling_setpoint": 25,
                "vpd_setpoint": 1,
                "co2_setpoint": 800,
            },
            {
                "start_time": "18:00",
                "heating_setpoint": 18,
                "cooling_setpoint": 27,
                "vpd_setpoint": 1.2,
                "co2_setpoint": 500,
            },
        ),
        light_targets=({"device_id": 7, "target_intensity": 60},),
    )
    snapshot = await MonitoringSnapshotBuilder(_repositories(fake)).build(
        MonitoringSnapshotRequest(
            location="Veg Room",
            cluster="main",
            now=NOW,
            runtime_snapshot_version=RuntimeSnapshotVersion(12),
        )
    )

    # When: existing pure climate and light logic is adapted to future intervals.
    intervals = project_future_intervals(snapshot)

    # Then: the complete 24-hour sequence is contract-valid; unavailable
    # series carry no value and never masquerade as estimated.
    assert validate_projection_timeline(intervals) == intervals
    assert intervals[0].valid_from == snapshot.range.start
    assert intervals[-1].valid_until == snapshot.range.end
    assert len(intervals) <= MAX_PROJECTION_INTERVALS
    assert intervals[0].version.config_version == 7
    assert intervals[0].version.revision == "000000c"
    assert all(
        point.quality is Quality.ESTIMATED if point.value is not None else point.quality is Quality.UNAVAILABLE
        for interval in intervals
        for point in interval.series
    )
    assert any(
        point.series_id.value == "light.photoperiod" and point.value is not None
        for interval in intervals
        for point in interval.series
    )
    assert all(
        not point.series_id.value.startswith(("device.", "pid."))
        for interval in intervals
        for point in interval.series
    )


@pytest.mark.anyio
async def test_missing_mode_or_target_is_unavailable() -> None:
    # Given: fake authority without a mode or target but with a publishable configuration version.
    fake = FakeSources()
    snapshot = await MonitoringSnapshotBuilder(_repositories(fake)).build(
        MonitoringSnapshotRequest(
            location="Flower Room",
            cluster="main",
            now=NOW,
            runtime_snapshot_version=RuntimeSnapshotVersion(12),
        )
    )

    # When: the adapter is asked to create publication-safe future facts.
    intervals = project_future_intervals(snapshot)

    # Then: every asserted future fact is explicitly unavailable rather than a fallback guess.
    assert intervals
    assert all(
        point.value is None and point.quality is Quality.UNAVAILABLE
        for point in intervals[0].series
    )


@pytest.mark.anyio
async def test_missing_configuration_version_refuses_projection_cache_replacement() -> None:
    # Given: otherwise readable authority with no configuration source version.
    fake = FakeSources(source_versions=(("configuration", None),))

    # When: the snapshot is adapted for publication.
    snapshot = await MonitoringSnapshotBuilder(_repositories(fake)).build(
        MonitoringSnapshotRequest("Flower Room", "main", NOW, RuntimeSnapshotVersion(12))
    )
    intervals = project_future_intervals(snapshot)

    # Then: no invalidly versioned projection can reach a replacement store.
    assert intervals == ()


@pytest.mark.anyio
async def test_dense_transition_authority_is_bounded_as_unavailable() -> None:
    # Given: more configured climate transitions than the publication budget permits.
    fake = FakeSources(
        active_mode={"mode_id": 1, "mode_name": "Veg"},
        mode_parameters={"mode_id": 1, "day_start_time": "06:00", "night_start_time": "18:00"},
        climate_periods=tuple(
            {"mode_id": 1, "start_time": f"{index // 60:02d}:{index % 60:02d}", "heating_setpoint": 20}
            for index in range(MAX_PROJECTION_INTERVALS + 1)
        ),
    )
    snapshot = await MonitoringSnapshotBuilder(_repositories(fake)).build(
        MonitoringSnapshotRequest("Veg Room", "main", NOW, RuntimeSnapshotVersion(12))
    )

    # When: pure interval projection exceeds its fixed output cap.
    intervals = project_future_intervals(snapshot)

    # Then: one complete unavailable interval preserves coverage without invented state.
    assert len(intervals) == 1
    assert (intervals[0].valid_from, intervals[0].valid_until) == (
        snapshot.range.start,
        snapshot.range.end,
    )
    assert all(point.quality is Quality.UNAVAILABLE for point in intervals[0].series)
