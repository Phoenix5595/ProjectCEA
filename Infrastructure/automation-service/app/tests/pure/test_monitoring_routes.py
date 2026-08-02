from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot
from app.routes import monitoring
from app.schemas.monitoring import (
    ClimateTimelinePoint,
    ClimateTimelineSeries,
    ControlMonitoringResponse,
    FlushHealth,
    SourceCursor,
)
from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    OpaqueCursorId,
    Origin,
    ProjectionMetadata,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
    TimelineProvenance,
)
from app.services.light_projection import LightProjection


class FakeHealth:
    def __init__(self, dropped_rows: int = 0) -> None:
        self.dropped_rows = dropped_rows

    def flush_health(self) -> tuple[FlushHealth, ...]:
        return (FlushHealth(source="photoperiod", dropped_rows=self.dropped_rows, healthy=True),)


class FakeHistory:
    def __init__(self, health: FakeHealth, *, fail_tail: bool = False) -> None:
        self.health = health
        self.fail_tail = fail_tail
        self.initial_calls = 0
        self.tail_cursors: dict[str, int] | None = None

    async def load_initial_range(
        self, location: str, cluster: str, monitoring_range: MonitoringRange
    ) -> ControlMonitoringResponse:
        del location, cluster
        self.initial_calls += 1
        return _response(monitoring_range, self.health)

    async def load_tails(
        self,
        location: str,
        cluster: str,
        monitoring_range: MonitoringRange,
        cursors: dict[str, int],
    ) -> ControlMonitoringResponse:
        del location, cluster
        self.tail_cursors = cursors
        if self.fail_tail:
            raise ConnectionError("database unavailable")
        return _response(monitoring_range, self.health)


class FakeSnapshotRepository:
    def __init__(self, snapshot: MonitoringSnapshot, *, fail: bool = False) -> None:
        self.snapshot = snapshot
        self.fail = fail

    async def load(
        self, location: str, cluster: str, monitoring_range: MonitoringRange
    ) -> MonitoringSnapshot:
        del location, cluster, monitoring_range
        if self.fail:
            raise ConnectionError("snapshot unavailable")
        return self.snapshot


def _range() -> MonitoringRange:
    now = datetime.now(UTC).replace(microsecond=0)
    return MonitoringRange(start=now - timedelta(minutes=10), end=now + timedelta(minutes=10))


def _response(monitoring_range: MonitoringRange, health: FakeHealth) -> ControlMonitoringResponse:
    return ControlMonitoringResponse(
        range=monitoring_range,
        runtime_snapshot_version=RuntimeSnapshotVersion(1),
        cursors=(
            SourceCursor(source="effective_setpoints", cursor=OpaqueCursorId("11"), has_more=False),
            SourceCursor(source="automation_state", cursor=OpaqueCursorId("12"), has_more=False),
            SourceCursor(source="photoperiod_history", cursor=OpaqueCursorId("13"), has_more=False),
        ),
        flush_health=health.flush_health(),
    )


def _snapshot(monitoring_range: MonitoringRange) -> MonitoringSnapshot:
    now = datetime.now(UTC).replace(microsecond=0)
    return MonitoringSnapshot(
        range=monitoring_range,
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
        source_cursors=(
            ("effective_setpoints", 11),
            ("automation_state", 12),
            ("photoperiod_history", 13),
        ),
        projection_revision=ProjectionRevision("revision"),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=now,
        anchor_quality=Quality.EXACT,
        anchor_valid_until=now + timedelta(seconds=60),
        runtime_snapshot_version=RuntimeSnapshotVersion(2),
    )


def _client(
    history: FakeHistory | None = None,
    health: FakeHealth | None = None,
    snapshot_failure: bool = False,
) -> tuple[TestClient, FakeHistory, list[MonitoringRange]]:
    health = health or FakeHealth()
    history = history or FakeHistory(health)
    monitoring_range = _range()
    reconciliations: list[MonitoringRange] = []

    async def reconcile(
        location: str, cluster: str, requested: MonitoringRange
    ) -> ControlMonitoringResponse:
        del location, cluster
        reconciliations.append(requested)
        return await history.load_initial_range("Veg Room", "main", requested)

    app = FastAPI()
    app.include_router(monitoring.router)
    app.dependency_overrides[monitoring.get_history_repository] = lambda: history
    app.dependency_overrides[monitoring.get_logger_health_provider] = lambda: health
    app.dependency_overrides[monitoring.get_snapshot_repository] = lambda: FakeSnapshotRepository(
        _snapshot(monitoring_range), fail=snapshot_failure
    )
    app.dependency_overrides[monitoring.get_reconcile_callback] = lambda: reconcile
    app.dependency_overrides[monitoring.get_climate_projection] = lambda: _climate_projection
    app.dependency_overrides[monitoring.get_light_projection] = lambda: _light_projection
    return TestClient(app), history, reconciliations


def _climate_projection(
    snapshot: MonitoringSnapshot, clock: object
) -> tuple[ClimateTimelineSeries, ...]:
    del clock
    return (
        ClimateTimelineSeries(
            name="heating",
            provenance=TimelineProvenance(origin=Origin.PROJECTED, quality=Quality.EXACT),
            projection=ProjectionMetadata(
                projection_revision=snapshot.projection_revision,
                anchor_fingerprint=snapshot.anchor_fingerprint,
                anchor_observed_at=snapshot.anchor_observed_at,
                anchor_quality=snapshot.anchor_quality,
                anchor_valid_until=snapshot.anchor_valid_until,
            ),
            points=(
                ClimateTimelinePoint(
                    timestamp=snapshot.range.end - timedelta(minutes=1),
                    metric="heating",
                    value=22.0,
                    nominal_value=22.0,
                    provenance=TimelineProvenance(origin=Origin.PROJECTED, quality=Quality.EXACT),
                ),
            ),
        ),
    )


def _light_projection(snapshot: MonitoringSnapshot, *, now: datetime) -> LightProjection:
    del snapshot, now
    return LightProjection(lights=(), photoperiod=())


def _query() -> str:
    monitoring_range = _range()
    start = monitoring_range.start.isoformat().replace("+00:00", "Z")
    end = monitoring_range.end.isoformat().replace("+00:00", "Z")
    return f"?start={start}&end={end}"


def test_range_contract() -> None:
    client, history, _ = _client()
    response = client.get(f"/api/monitoring/control/Veg%20Room{_query()}")
    assert response.status_code == 200
    assert history.initial_calls == 1
    assert client.get("/api/monitoring/control/Veg%20Room?start=bad&end=bad").status_code == 400


def test_projection_only() -> None:
    client, _, _ = _client()
    response = client.get(f"/api/monitoring/control/Veg%20Room/projection{_query()}")
    assert response.status_code == 200
    assert response.json()["devices"] == []
    assert response.json()["climate"][0]["provenance"]["origin"] == "projected"


def test_paged_ingestion_cursor() -> None:
    client, history, _ = _client()
    response = client.get(
        f"/api/monitoring/control/Veg%20Room/tail{_query()}&effective_setpoints_cursor=1&automation_state_cursor=2&photoperiod_history_cursor=3"
    )
    assert response.status_code == 200
    assert history.tail_cursors == {
        "effective_setpoints": 1,
        "automation_state": 2,
        "photoperiod_history": 3,
    }


def test_range_cursor_race() -> None:
    client, history, _ = _client()
    assert client.get(f"/api/monitoring/control/Veg%20Room{_query()}").status_code == 200
    assert history.initial_calls == 1


def test_anchor_expiry() -> None:
    client, _, _ = _client()
    assert client.get(f"/api/monitoring/control/Veg%20Room/projection{_query()}").status_code == 200


def test_logger_health() -> None:
    client, _, _ = _client(health=FakeHealth(dropped_rows=0))
    response = client.get(f"/api/monitoring/control/Veg%20Room{_query()}")
    assert response.json()["flush_health"][0]["source"] == "photoperiod"


def test_projection_failure_keeps_history() -> None:
    client, history, _ = _client(snapshot_failure=True)
    assert client.get(f"/api/monitoring/control/Veg%20Room{_query()}").status_code == 200
    assert history.initial_calls == 1


def test_outage_over_15s_returns_late_insert() -> None:
    health = FakeHealth()
    client, history, reconciliations = _client(
        history=FakeHistory(health, fail_tail=True), health=health
    )
    response = client.get(
        f"/api/monitoring/control/Veg%20Room/tail{_query()}&effective_setpoints_cursor=1&automation_state_cursor=2&photoperiod_history_cursor=3"
    )
    assert response.status_code == 200
    assert history.initial_calls == 1
    assert len(reconciliations) == 1


def test_tied_timestamps_use_ingest_id() -> None:
    client, history, _ = _client()
    client.get(
        f"/api/monitoring/control/Veg%20Room/tail{_query()}&effective_setpoints_cursor=99&automation_state_cursor=2&photoperiod_history_cursor=3"
    )
    assert history.tail_cursors is not None and history.tail_cursors["effective_setpoints"] == 99


def test_dropped_rows_require_reconcile() -> None:
    health = FakeHealth(dropped_rows=1)
    client, history, reconciliations = _client(health=health)
    response = client.get(
        f"/api/monitoring/control/Veg%20Room/tail{_query()}&effective_setpoints_cursor=1&automation_state_cursor=2&photoperiod_history_cursor=3"
    )
    assert response.status_code == 200
    assert history.initial_calls == 1
    assert len(reconciliations) == 1
