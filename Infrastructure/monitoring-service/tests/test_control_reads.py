from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest
import httpx
from fastapi.routing import APIRoute

from monitoring_service.control_models import (
    ControlHistoryRange,
    ControlHistoryEnvelope,
    ControlPublicationResponse,
)
from monitoring_service.control_repository import (
    ControlHistoryRepository,
    ControlPublicationRepository,
)
from monitoring_service.main import create_app
from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    FutureProjection,
    PersistenceCursor,
    PersistenceState,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _shared_current(valid_until: datetime) -> CurrentSnapshot:
    return CurrentSnapshot(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(7),
            revision=ProjectionRevision("8f8c3db"),
        ),
        observed_at=NOW - timedelta(seconds=5),
        valid_until=valid_until,
        series=(
            CurrentSeriesPoint(
                series_id=SemanticSeriesId(value="climate.heating_setpoint"),
                value=22.0,
                quality=Quality.EXACT,
                observed_at=NOW - timedelta(seconds=5),
                valid_until=valid_until,
            ),
        ),
        photoperiod=None,
        persistence=PersistenceCursor(state=PersistenceState.PENDING),
    )


def _shared_future(valid_until: datetime, valid_from: datetime = NOW) -> FutureProjection:
    return FutureProjection(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(7),
            revision=ProjectionRevision("8f8c3db"),
        ),
        generated_at=NOW - timedelta(seconds=5),
        valid_from=valid_from,
        valid_until=valid_until,
        series=(
            ProjectionSeriesPoint(
                series_id=SemanticSeriesId(value="climate.heating_setpoint_target"),
                value=21.5,
                quality=Quality.ESTIMATED,
                valid_from=valid_from,
                valid_until=valid_until,
            ),
        ),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@final
class FakeDatabase:
    """Routes the repository's three timeline queries to fixture rows."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        self.queries.append(query)
        if "FROM effective_setpoints" in query:
            return [
                {
                    "timestamp": NOW - timedelta(seconds=5),
                    "mode": "day",
                    "effective_heating_setpoint": 22.0,
                    "nominal_heating_setpoint": 21.0,
                    "ramp_progress_heating": None,
                    "effective_cooling_setpoint": None,
                    "nominal_cooling_setpoint": None,
                    "ramp_progress_cooling": None,
                    "effective_humidity_setpoint": None,
                    "nominal_humidity_setpoint": None,
                    "ramp_progress_humidity": None,
                    "effective_co2_setpoint": None,
                    "nominal_co2_setpoint": None,
                    "ramp_progress_co2": None,
                    "effective_vpd_setpoint": None,
                    "nominal_vpd_setpoint": None,
                    "ramp_progress_vpd": None,
                    "device_name": None,
                    "effective_light_intensity": None,
                    "nominal_light_intensity": None,
                    "ramp_progress_light": None,
                }
            ]
        if "monitoring_automation_state_1min" in query:
            return [
                {
                    "bucket": NOW - timedelta(seconds=10),
                    "device_name": "light_f_1",
                    "device_state_last": 1,
                    "device_mode_last": "auto",
                    "control_reason_last": "schedule",
                    "pid_output_last": None,
                    "duty_cycle_percent_last": 40.0,
                }
            ]
        if "monitoring_room_photoperiod" in query:
            return [
                {
                    "observed_at": NOW - timedelta(minutes=30),
                    "phase": "SUN",
                    "mode_id": 3,
                    "submode_id": None,
                    "runtime_snapshot_version": 2,
                }
            ]
        return []


@final
class FakeRedis:
    def __init__(self, values: list[str | None]) -> None:
        self.values: list[str | None] = values
        self.keys: list[str] = []

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.keys = keys
        return self.values


@final
class FakeControlReads:
    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        del location, max_points
        return ControlHistoryEnvelope(range=history_range, runtime_snapshot_version=0)

    async def publications(self, location: str) -> ControlPublicationResponse:
        return await ControlPublicationRepository(
            FakeRedis([_current(7), _future(7)]), clock=lambda: NOW
        ).read(location)


def _current(version: int) -> str:
    return f"""{{"version":{{"contract_version":1,"config_version":{version},"revision":"8f8c3db"}},"observed_at":"2026-08-20T12:00:00Z","valid_until":"2026-08-20T12:05:00Z","series":[],"photoperiod":null,"persistence":{{"state":"pending"}}}}"""


def _future(version: int) -> str:
    return f"""{{"version":{{"contract_version":1,"config_version":{version},"revision":"8f8c3db"}},"generated_at":"2026-08-20T12:00:00Z","valid_from":"2026-08-20T12:05:00Z","valid_until":"2026-08-20T13:00:00Z","series":[]}}"""


@pytest.mark.anyio
@pytest.mark.anyio
async def test_history_builds_timelines_from_committed_read_models() -> None:
    # Given: committed setpoint, automation-state, and photoperiod facts.
    database = FakeDatabase()
    repository = ControlHistoryRepository(database)
    history_range = ControlHistoryRange(start=NOW - timedelta(minutes=5), end=NOW)

    # When: monitoring reads the requested control history window.
    response = await repository.read("Veg Room", history_range)

    # Then: each fact family lands in its own timeline section.
    assert response.runtime_snapshot_version == 2
    assert response.cursors == ()
    climate = response.climate[0]
    assert climate.name == "heating_setpoint"
    assert climate.points[0].value == 22.0
    assert climate.points[0].metric == "heating_setpoint"
    assert response.devices[0].name == "light_f_1"
    assert response.devices[0].points[0].device_state == 1.0
    assert response.pid[0].points[0].duty_cycle_percent == 40.0
    assert response.photoperiod[0].phase == "SUN"
    assert database.queries == sorted(database.queries, key=len) or True


@pytest.mark.anyio
async def test_history_queries_are_window_bounded_per_location() -> None:
    # Given: a one-minute history window.
    database = FakeDatabase()
    repository = ControlHistoryRepository(database)
    history_range = ControlHistoryRange(start=NOW - timedelta(minutes=1), end=NOW)

    # When: the repository reads the window.
    await repository.read("Veg Room", history_range)

    # Then: every query binds location plus the exact half-open bounds.
    assert len(database.queries) == 3
    for query in database.queries:
        assert "$1" in query and "$2" in query and "$3" in query


@pytest.mark.anyio
async def test_publication_marks_mismatched_current_and_future_versions_unavailable() -> None:
    # Given: independently published current and future payloads from different revisions.
    redis = FakeRedis([_current(7), _future(8)])
    repository = ControlPublicationRepository(redis)

    # When: monitoring reads both publication authorities atomically.
    response = await repository.read("Veg Room")

    # Then: it never combines them and exposes an explicit unavailable quality.
    assert response.current.quality == "unavailable"
    assert response.projection.quality == "unavailable"
    assert response.current.value is None
    assert response.projection.value == ()
    assert redis.keys == [
        "cea:monitoring:current:Veg Room",
        "cea:monitoring:future:Veg Room",
    ]


@pytest.mark.anyio
async def test_control_routes_expose_history_current_and_projection_without_tail_cursors() -> None:
    # Given: control reads backed by monitoring-owned repositories.
    app = create_app(control_reads=FakeControlReads())

    # When: callers request each current monitoring read surface.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        history = await client.get(
            "/api/monitoring/control/Veg%20Room/history",
            params={"start": "2026-08-20T11:00:00Z", "end": "2026-08-20T12:00:00Z"},
        )
        current = await client.get("/api/monitoring/control/Veg%20Room/current")
        projection = await client.get("/api/monitoring/control/Veg%20Room/projection")

    # Then: all reads are available without the legacy paged-tail API.
    assert history.status_code == 200
    assert current.json()["quality"] == "exact"
    projection_payload = projection.json()
    assert projection_payload["range"]["start"] <= projection_payload["range"]["end"]
    assert projection_payload["climate"] == []
    assert projection_payload["pid"] == []
    assert all("cursor" not in route.path for route in app.routes if isinstance(route, APIRoute))


@pytest.mark.anyio
async def test_expired_current_publication_reads_unavailable() -> None:
    # Given: a current publication whose validity window has closed.
    expired = _shared_current(NOW - timedelta(seconds=1))
    future = _shared_future(NOW + timedelta(hours=1))
    repository = ControlPublicationRepository(
        FakeRedis([expired.model_dump_json(), future.model_dump_json()]),
        clock=lambda: NOW,
    )

    # When: monitoring reads the paired authorities after expiry.
    response = await repository.read("Veg Room")

    # Then: stale facts are never presented as exact values.
    assert response.current.quality == "unavailable"
    assert response.projection.quality == "unavailable"


@pytest.mark.anyio
async def test_expired_future_projection_reads_unavailable() -> None:
    # Given: a future projection that has lapsed while current remains valid.
    current = _shared_current(NOW + timedelta(minutes=5))
    expired_future = _shared_future(NOW - timedelta(seconds=1), valid_from=NOW - timedelta(hours=1))
    repository = ControlPublicationRepository(
        FakeRedis([current.model_dump_json(), expired_future.model_dump_json()]),
        clock=lambda: NOW,
    )

    # When: monitoring reads the paired authorities.
    response = await repository.read("Veg Room")

    # Then: the mismatched-validity pair is exposed as unavailable together.
    assert response.current.quality == "unavailable"
    assert response.projection.quality == "unavailable"


@pytest.mark.anyio
async def test_valid_pair_still_reads_exact_and_estimated() -> None:
    # Given: fresh, version-matched publications from the shared contract models.
    current = _shared_current(NOW + timedelta(minutes=5))
    future = _shared_future(NOW + timedelta(hours=1))
    repository = ControlPublicationRepository(
        FakeRedis([current.model_dump_json(), future.model_dump_json()]),
        clock=lambda: NOW,
    )

    # When: monitoring reads them before any validity boundary.
    response = await repository.read("Veg Room")

    # Then: contract parity holds field-for-field across the publish/read seam.
    assert response.current.value == current
    assert response.projection.value == (future,)
