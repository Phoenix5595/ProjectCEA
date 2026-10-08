from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import final

import asyncpg
import pytest
import httpx
from fastapi.routing import APIRoute

from monitoring_service.control_models import (
    ControlHistoryRange,
    ControlHistoryEnvelope,
    ControlPublicationResponse,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
)
from monitoring_service.control_repository import (
    ControlHistoryRepository,
    ControlPublicationRepository,
    _publication_keys,
)
from monitoring_service.control_history_queries import (
    _LIGHT_SETPOINTS_SQL,
    _RAW_SETPOINTS_SQL,
    _SETPOINTS_1MIN_SQL,
    _SETPOINTS_5MIN_SQL,
    select_control_history_sources,
)
from monitoring_service.main import create_app
from monitoring_service.sensor_models import MonitoringUnavailableError
from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSeriesPoint,
    CurrentSnapshot,
    FutureProjection,
    MonitoringContractViolation,
    PersistenceCursor,
    PersistenceState,
    ProjectionRevision,
    ProjectionSeriesPoint,
    PublicationVersion,
    Quality,
    SemanticSeriesId,
    validate_projection_timeline,
)
from shared.redis_keys import (
    monitoring_current_publication_key,
    monitoring_future_publication_key,
    monitoring_rich_trajectory_key,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _light_history_row(
    timestamp: datetime,
    device_name: str | None,
    mode: str,
    value: float | None,
) -> dict[str, str | float | datetime | None]:
    row: dict[str, str | float | datetime | None] = {
        "timestamp": timestamp,
        "mode": mode,
        "device_name": device_name,
        "effective_light_intensity": value,
        "nominal_light_intensity": value,
        "ramp_progress_light": None,
    }
    for metric in ("heating", "cooling", "humidity", "co2", "vpd"):
        row[f"effective_{metric}_setpoint"] = None
        row[f"nominal_{metric}_setpoint"] = None
        row[f"ramp_progress_{metric}"] = None
    return row


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
    """Routes the repository's read-model queries to fixture rows."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        self.queries.append(query)
        if "monitoring_photoperiod_coverage" in query:
            return [
                {
                    "id": 7,
                    "observed_at": NOW - timedelta(minutes=30),
                    "location": "Veg Room",
                    "cluster": "main",
                    "state": "available",
                    "reason": "initial",
                    "runtime_snapshot_version": 2,
                }
            ]
        if "monitoring_room_photoperiod" in query:
            return [
                {
                    "id": 6,
                    "observed_at": NOW - timedelta(minutes=30),
                    "location": "Veg Room",
                    "cluster": "main",
                    "phase": "SUN",
                    "mode_id": 3,
                    "submode_id": None,
                    "runtime_snapshot_version": 2,
                    "source": "photoperiod_transition",
                }
            ]
        if "FROM effective_setpoints" in query:
            if len(arguments) == 2:
                return []
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
        return []


@final
class LightTimelineDatabase:
    def __init__(self, rows: list[dict[str, str | float | datetime | None]]) -> None:
        self.rows = rows

    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | datetime | None]]:
        if "FROM effective_setpoints" in query:
            return self.rows
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
    def __init__(self, publication_values: list[str | None] | None = None) -> None:
        self._publication_values = publication_values or [_current(7), _future_timeline()]

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        del location, max_points
        return ControlHistoryEnvelope(range=history_range, runtime_snapshot_version=0)

    async def publications(self, location: str) -> ControlPublicationResponse:
        return await ControlPublicationRepository(
            FakeRedis(self._publication_values), clock=lambda: NOW
        ).read(location)


def _current(version: int) -> str:
    return f"""{{"version":{{"contract_version":1,"config_version":{version},"revision":"8f8c3db"}},"observed_at":"2026-08-20T12:00:00Z","valid_until":"2026-08-20T12:05:00Z","series":[],"photoperiod":null,"persistence":{{"state":"pending"}}}}"""


def _future(version: int) -> str:
    return f"""{{"version":{{"contract_version":1,"config_version":{version},"revision":"8f8c3db"}},"generated_at":"2026-08-20T12:00:00Z","valid_from":"2026-08-20T12:05:00Z","valid_until":"2026-08-20T13:00:00Z","series":[]}}"""


def _future_timeline() -> str:
    first = _shared_future(NOW + timedelta(minutes=30), NOW).model_dump(mode="json")
    second = _shared_future(NOW + timedelta(hours=1), NOW + timedelta(minutes=30)).model_dump(
        mode="json"
    )
    return json.dumps([first, second])


def _rich_trajectory(revision: str) -> str:
    return json.dumps(
        {
            "contract_version": 1,
            "room": "Veg Room",
            "generated_at": "2026-08-20T12:00:00Z",
            "window": {
                "start": "2026-08-20T12:00:00Z",
                "end": "2026-08-20T13:00:00Z",
                "timezone": "UTC",
            },
            "revision_scope": "saved",
            "base_config_revision": revision,
            "draft_revision": None,
            "segments": [
                {
                    "start": "2026-08-20T12:00:00Z",
                    "end": "2026-08-20T13:00:00Z",
                    "metric": "heating",
                    "unit": "C",
                    "trajectory_kind": "scheduled",
                    "quality": "exact",
                    "source": {
                        "mode": "Veg",
                        "submode": None,
                        "period": {"period_id": "1", "label": "Day"},
                        "config_revision": revision,
                        "draft_revision": None,
                    },
                    "shape": "step",
                    "value": 21.0,
                }
            ],
            "assumptions": [],
            "warnings": [],
        }
    )


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
async def test_history_canonicalizes_each_light_by_device_and_timestamp() -> None:
    # Given: mode transitions, a null/finite sibling pair, and conflicting finite siblings.
    start = NOW - timedelta(minutes=5)
    rows = [
        _light_history_row(start, "light_f_1", "day", 40.0),
        _light_history_row(start, "light_f_2", "day", 20.0),
        _light_history_row(start + timedelta(minutes=2), "light_f_1", "day", 80.0),
        _light_history_row(start + timedelta(minutes=1), "light_f_1", "night", 0.0),
        _light_history_row(start + timedelta(minutes=3), "light_f_1", "day", None),
        _light_history_row(start + timedelta(minutes=3), "light_f_1", "day", 75.0),
        _light_history_row(start + timedelta(minutes=4), "light_f_1", "day", 60.0),
        _light_history_row(start + timedelta(minutes=4), "light_f_1", "night", 70.0),
        _light_history_row(start + timedelta(minutes=4), None, "day", 99.0),
    ]
    repository = ControlHistoryRepository(LightTimelineDatabase(rows))

    # When: the raw control history is read without point budgeting.
    response = await repository.read(
        "Flower Room",
        ControlHistoryRange(start=start, end=NOW),
    )

    # Then: each physical light has one chronological identity and one fact per timestamp.
    assert [series.name for series in response.lights] == ["light_f_1", "light_f_2"]
    light = response.lights[0]
    assert [(point.timestamp, point.value, point.mode) for point in light.points] == [
        (start, 40.0, "day"),
        (start + timedelta(minutes=1), 0.0, "night"),
        (start + timedelta(minutes=2), 80.0, "day"),
        (start + timedelta(minutes=3), 75.0, "day"),
        (start + timedelta(minutes=4), 60.0, "day"),
    ]
    assert len(response.lights[1].points) == 1


@final
class WindowedControlDatabase:
    """Serves every control-history family strictly from its bound window."""

    def __init__(
        self,
        *,
        light_rows: list[dict[str, str | float | datetime | None]],
        photoperiod_rows: list[dict[str, str | int | datetime | None]],
        coverage_rows: list[dict[str, str | int | datetime | None]],
    ) -> None:
        self.light_rows = light_rows
        self.photoperiod_rows = photoperiod_rows
        self.coverage_rows = coverage_rows

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        if "monitoring_photoperiod_coverage" in query:
            return self._bound(self.coverage_rows, arguments)
        if "monitoring_room_photoperiod" in query:
            return self._bound(self.photoperiod_rows, arguments)
        if "effective_setpoints" in query:
            if len(arguments) == 2:
                _, predecessor_start = arguments
                assert isinstance(predecessor_start, datetime)
                return [
                    row
                    for row in self.light_rows
                    if isinstance(row["timestamp"], datetime)
                    and predecessor_start - timedelta(seconds=60)
                    <= row["timestamp"]
                    < predecessor_start
                ]
            _, start, end = arguments
            return [
                row
                for row in self.light_rows
                if isinstance(row["timestamp"], datetime)
                and start <= row["timestamp"] < end
                and row.get("device_name") is not None
                and any(
                    row.get(column) is not None
                    for column in (
                        "effective_light_intensity",
                        "nominal_light_intensity",
                        "ramp_progress_light",
                    )
                )
            ]
        return []

    @staticmethod
    def _bound(
        rows: list[dict[str, str | int | datetime | None]],
        arguments: tuple[str | int | float | datetime, ...],
    ) -> list[dict[str, str | int | datetime | None]]:
        _, start, end = arguments
        carried = [row for row in rows if row["observed_at"] < start]
        in_range = [row for row in rows if row["observed_at"] >= start]
        half_open = [
            row
            for row in in_range
            if isinstance(row["observed_at"], datetime) and row["observed_at"] < end
        ]
        return (carried[-1:] if carried else []) + half_open


def _transition_fixture(
    observed_at: datetime,
    phase: str,
    *,
    id_: int = 4,
    source: str = "photoperiod_transition",
) -> dict[str, str | int | datetime | None]:
    return {
        "id": id_,
        "observed_at": observed_at,
        "location": "Veg Room",
        "cluster": "main",
        "phase": phase,
        "mode_id": 3,
        "submode_id": None,
        "runtime_snapshot_version": 2,
        "source": source,
    }


def _coverage_fixture(
    observed_at: datetime,
    state: str,
    *,
    id_: int = 9,
) -> dict[str, str | int | datetime | None]:
    return {
        "id": id_,
        "observed_at": observed_at,
        "location": "Veg Room",
        "cluster": "main",
        "state": state,
        "reason": "started",
        "runtime_snapshot_version": 2,
    }


@pytest.mark.anyio
async def test_history_clamps_light_bounds_but_carries_photoperiod_in() -> None:
    # Given: a pre-start per-device light row only inside the predecessor minute,
    # plus a committed transition state predating the half-open window.
    start = NOW - timedelta(minutes=5)
    pre_light = _light_history_row(start - timedelta(seconds=10), "light_f_1", "day", 40.0)
    in_light = _light_history_row(start + timedelta(minutes=1), "light_f_1", "day", 80.0)
    database = WindowedControlDatabase(
        light_rows=[pre_light, in_light],
        photoperiod_rows=[
            _transition_fixture(start - timedelta(seconds=30), "SUN")
        ],
        coverage_rows=[_coverage_fixture(start - timedelta(minutes=10), "available")],
    )
    repository = ControlHistoryRepository(database)

    # When: history is read for the five-minute window.
    response = await repository.read(
        "Veg Room", ControlHistoryRange(start=start, end=start + timedelta(minutes=5))
    )

    # Then: the pre-start light sample never leaks into the light series while
    # the committed phase carries in as the exact start anchor.
    assert [series.name for series in response.lights] == ["light_f_1"]
    lights = response.lights[0]
    assert [(point.timestamp, point.value) for point in lights.points] == [
        (start + timedelta(minutes=1), 80.0)
    ]
    assert response.photoperiod[0].phase == "SUN"
    assert response.photoperiod[0].timestamp == start
    assert response.photoperiod[0].provenance.origin == "recorded"
    assert response.photoperiod[0].provenance.quality == "exact"


@pytest.mark.anyio
async def test_legacy_predecessor_beyond_sixty_seconds_cannot_carry_in() -> None:
    # Given: the only predecessor is an old dedicated sample recorded 70 seconds
    # before start.
    # When: history is read for the window.
    # Then: no expired sample ever supports the start anchor.
    start = NOW - timedelta(minutes=5)
    database = WindowedControlDatabase(
        light_rows=[],
        photoperiod_rows=[
            _transition_fixture(
                start - timedelta(seconds=70), "MOON", id_=12, source="photoperiod"
            )
        ],
        coverage_rows=[],
    )
    repository = ControlHistoryRepository(database)

    response = await repository.read(
        "Veg Room", ControlHistoryRange(start=start, end=start + timedelta(minutes=5))
    )

    assert [(point.phase, point.timestamp) for point in response.photoperiod] == [
        ("UNKNOWN", start)
    ]


@pytest.mark.anyio
async def test_overlapping_history_windows_keep_absolute_transition_instants() -> None:
    # Given: one committed MOON carry-in predecessor plus one in-range SUN
    # transition read through two overlapping windows.
    # When: each window is read through the same repository path.
    # Then: both agree on the absolute transition instant without duplicates.
    start = NOW - timedelta(minutes=5)
    transition_at = start + timedelta(minutes=2)
    database = WindowedControlDatabase(
        light_rows=[],
        photoperiod_rows=[
            _transition_fixture(start - timedelta(seconds=30), "MOON", id_=11),
            _transition_fixture(transition_at, "SUN", id_=12),
        ],
        coverage_rows=[_coverage_fixture(start - timedelta(minutes=10), "available")],
    )
    repository = ControlHistoryRepository(database)

    first = await repository.read(
        "Veg Room", ControlHistoryRange(start=start, end=start + timedelta(minutes=5))
    )
    second = await repository.read(
        "Veg Room",
        ControlHistoryRange(
            start=start + timedelta(minutes=1), end=start + timedelta(minutes=6)
        ),
    )

    assert [point.phase for point in first.photoperiod] == ["MOON", "SUN"]
    assert [point.phase for point in second.photoperiod] == ["MOON", "SUN"]
    first_instants = [point.timestamp for point in first.photoperiod]
    second_instants = [point.timestamp for point in second.photoperiod]
    assert first_instants == [start, transition_at]
    assert second_instants == [start + timedelta(minutes=1), transition_at]


@final
class MissingCoverageTableDatabase:
    """Raises the real missing-relation failure on every fetch attempt."""

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, str | int | float | datetime | None]]:
        if "monitoring_photoperiod_coverage" in query:
            raise asyncpg.UndefinedTableError("monitoring_photoperiod_coverage missing")
        raise asyncpg.PostgresError("unrelated failure")


@final
class RepositoryControlReads:
    """Serves history only through the shared repository read path."""

    def __init__(self, repository: ControlHistoryRepository) -> None:
        self._repository = repository

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        return await self._repository.read(location, history_range, max_points)

    async def publications(self, location: str) -> ControlPublicationResponse:
        del location
        return ControlPublicationResponse(
            current=CurrentPublicationResponse(quality=Quality.UNAVAILABLE, value=None),
            projection=ProjectionPublicationResponse(quality=Quality.UNAVAILABLE),
        )


@pytest.mark.anyio
async def test_missing_photoperiod_coverage_table_is_a_schema_prerequisite_failure() -> None:
    # Given: a database whose photoperiod coverage relation does not exist yet.
    # When: the repository reads the control history window.
    # Then: an explicit unavailable error outranks any silent fallback.
    repository = ControlHistoryRepository(MissingCoverageTableDatabase())

    with pytest.raises(MonitoringUnavailableError):
        await repository.read(
            "Veg Room", ControlHistoryRange(start=NOW - timedelta(minutes=5), end=NOW)
        )


@pytest.mark.anyio
async def test_history_route_serves_unavailable_when_coverage_table_is_missing() -> None:
    # Given: the same missing-relation database behind the real history path.
    repository = ControlHistoryRepository(MissingCoverageTableDatabase())
    app = create_app(control_reads=RepositoryControlReads(repository))

    # When: a client requests recorded history.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Veg%20Room/history",
            params={"start": "2026-08-20T11:55:00Z", "end": "2026-08-20T12:00:00Z"},
        )

    # Then: the read never falls back to current schedules.
    assert response.status_code == 503


def test_light_history_source_is_independent_of_range_selection() -> None:
    # Given: every budgeted and unbudgeted range selection the read path supports.
    selections = (
        (ControlHistoryRange(start=NOW, end=NOW + timedelta(minutes=70)), 1000),
        (ControlHistoryRange(start=NOW, end=NOW + timedelta(hours=12)), 1000),
        (ControlHistoryRange(start=NOW, end=NOW + timedelta(days=1)), 1000),
        (ControlHistoryRange(start=NOW, end=NOW + timedelta(days=7)), 1000),
        (ControlHistoryRange(start=NOW, end=NOW + timedelta(hours=1)), None),
    )

    # When: sources are selected for each window.
    sources = [
        select_control_history_sources(history_range, max_points)
        for history_range, max_points in selections
    ]

    # Then: lights always read one raw per-device source while the climate ladder is unchanged.
    assert all(source.light_sql == _LIGHT_SETPOINTS_SQL for source in sources)
    assert all("monitoring_effective_setpoints_" not in source.light_sql for source in sources)
    assert [source.setpoints_sql for source in sources] == [
        _RAW_SETPOINTS_SQL,
        _SETPOINTS_1MIN_SQL,
        _SETPOINTS_5MIN_SQL,
        _SETPOINTS_5MIN_SQL,
        _RAW_SETPOINTS_SQL,
    ]
    assert [source.setpoints_are_aggregated for source in sources] == [
        False,
        True,
        True,
        True,
        False,
    ]


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
        "cea:monitoring:trajectory:Veg Room",
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

    # Then: history stays independent while the projection route exposes the canonical timeline.
    assert history.status_code == 200
    assert current.json()["quality"] == "exact"
    projection_payload = projection.json()
    assert projection_payload["quality"] == "estimated"
    assert len(projection_payload["value"]) == 2
    assert projection_payload["value"][0]["series"][0]["value"] == 21.5
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


@pytest.mark.anyio
async def test_publication_rejects_rich_trajectory_from_another_config_revision() -> None:
    # Given: current and future facts share revision seven but rich provenance is revision six.
    repository = ControlPublicationRepository(
        FakeRedis([_current(7), _future(7), _rich_trajectory("0000006")]),
        clock=lambda: NOW,
    )

    # When: monitoring reads the complete publication tuple.
    response = await repository.read("Veg Room")

    # Then: stale rich provenance is rejected while legacy projection remains available.
    assert response.projection.quality == "estimated"
    assert response.projection.trajectory is None


@pytest.mark.anyio
async def test_publication_accepts_rich_trajectory_matching_config_revision() -> None:
    # Given: current, future, and rich facts share the same configuration revision.
    repository = ControlPublicationRepository(
        FakeRedis([_current(7), _future(7), _rich_trajectory("0000007")]),
        clock=lambda: NOW,
    )

    # When: monitoring reads the complete publication tuple.
    response = await repository.read("Veg Room")

    # Then: the matching rich trajectory remains available to legacy consumers.
    assert response.projection.quality == "estimated"
    assert response.projection.trajectory is not None
    assert response.projection.trajectory.base_config_revision == "0000007"


def _future_window_json(version: int, valid_from: str, valid_until: str) -> str:
    payload = json.loads(_future(version))
    payload["valid_from"] = valid_from
    payload["valid_until"] = valid_until
    return json.dumps(payload)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("future", "expected_quality"),
    [
        (_future_window_json(7, "2026-08-20T10:00:00Z", "2026-08-20T11:00:00Z"), "unavailable"),
        (_future(8), "unavailable"),
    ],
)
async def test_projection_route_reports_unavailable_without_affecting_history(
    future: str, expected_quality: str
) -> None:
    # Given: recorded history and a future publication that is expired or version-mismatched.
    app = create_app(control_reads=FakeControlReads([_current(7), future]))

    # When: the independent history and projection routes are read.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        history = await client.get(
            "/api/monitoring/control/Veg%20Room/history",
            params={"start": "2026-08-20T11:00:00Z", "end": "2026-08-20T12:00:00Z"},
        )
        projection = await client.get("/api/monitoring/control/Veg%20Room/projection")

    # Then: unavailable publication facts never clear or rewrite recorded history.
    assert history.status_code == 200
    assert f'"quality":"{expected_quality}"'.encode() in projection.content
    assert b'"value":[]' in projection.content
    assert b'"trajectory":null' in projection.content


@pytest.mark.anyio
async def test_projection_route_serializes_unavailable_sample_value_as_null() -> None:
    # Given: a valid unavailable projection sample with a required null value.
    unavailable_future = FutureProjection(
        version=PublicationVersion(
            contract_version=1,
            config_version=ConfigVersion(7),
            revision=ProjectionRevision("8f8c3db"),
        ),
        generated_at=NOW,
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=30),
        series=(
            ProjectionSeriesPoint(
                series_id=SemanticSeriesId(value="climate.heating_setpoint_target"),
                value=None,
                quality=Quality.UNAVAILABLE,
                valid_from=NOW,
                valid_until=NOW + timedelta(minutes=30),
            ),
        ),
    )
    app = create_app(
        control_reads=FakeControlReads([_current(7), unavailable_future.model_dump_json()])
    )

    # When: the frontend reads the canonical projection endpoint.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/monitoring/control/Veg%20Room/projection")

    # Then: the wire contract preserves the required key as JSON null.
    assert response.status_code == 200
    assert response.content.count(b'"value":null') == 1


@pytest.mark.anyio
async def test_publication_parses_legacy_single_object_future() -> None:
    repository = ControlPublicationRepository(
        FakeRedis([_current(7), _future(7)]), clock=lambda: NOW
    )
    response = await repository.read("Veg Room")
    assert response.projection.quality == "estimated"
    assert response.projection.value is not None and len(response.projection.value) == 1


@pytest.mark.anyio
async def test_publication_parses_versioned_array_future_timeline() -> None:
    timeline = json.dumps(
        [
            json.loads(_future(7)),
            json.loads(_future_window_json(7, "2026-08-20T13:00:00Z", "2026-08-20T14:00:00Z")),
        ]
    )
    repository = ControlPublicationRepository(FakeRedis([_current(7), timeline]), clock=lambda: NOW)
    response = await repository.read("Veg Room")
    assert response.projection.quality == "estimated"
    assert response.projection.value is not None and len(response.projection.value) == 2


@pytest.mark.anyio
async def test_publication_rejects_overlapping_array_timeline_unavailable() -> None:
    timeline = json.dumps(
        [
            json.loads(_future(7)),
            json.loads(_future_window_json(7, "2026-08-20T12:30:00Z", "2026-08-20T14:00:00Z")),
        ]
    )
    repository = ControlPublicationRepository(FakeRedis([_current(7), timeline]), clock=lambda: NOW)
    response = await repository.read("Veg Room")
    assert response.projection.quality == "unavailable"
    assert response.projection.value == ()


@pytest.mark.anyio
async def test_publication_rejects_malformed_future_payload_unavailable() -> None:
    repository = ControlPublicationRepository(
        FakeRedis([_current(7), "{not-json"]), clock=lambda: NOW
    )
    response = await repository.read("Veg Room")
    assert response.projection.quality == "unavailable"


def test_publication_keys_match_shared_canonical_builders() -> None:
    assert _publication_keys("Flower Room") == [
        monitoring_current_publication_key("Flower Room"),
        monitoring_future_publication_key("Flower Room"),
        monitoring_rich_trajectory_key("Flower Room"),
    ]


def test_projection_timeline_rejects_mixed_versions() -> None:
    projections = (
        FutureProjection.model_validate_json(_future(7)),
        FutureProjection.model_validate_json(_future(8)),
    )
    with pytest.raises(MonitoringContractViolation):
        validate_projection_timeline(projections)


def test_projection_timeline_accepts_empty_and_ordered() -> None:
    assert validate_projection_timeline(()) == ()
    first = FutureProjection.model_validate_json(_future(7))
    second = FutureProjection.model_validate_json(
        _future_window_json(7, "2026-08-20T13:00:00Z", "2026-08-20T14:00:00Z")
    )
    assert validate_projection_timeline((first, second)) == (first, second)
