from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import final
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
)
from monitoring_service.main import create_app
from monitoring_service.sensor_models import (
    MonitoringRange,
    Node,
    SensorSeries,
    SensorStatistics,
    SeriesPoint,
    Tier,
    UnitFamily,
    derive_interval_seconds,
)
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_routes import get_sensor_reads

NOW = datetime(2026, 8, 24, 14, tzinfo=UTC)


@final
class FakeSensorReads:
    async def series(
        self, room: str, monitoring_range: MonitoringRange, max_points: int | None = None
    ) -> tuple[Tier, tuple[SensorSeries, ...]]:
        del room, monitoring_range, max_points
        return (
            Tier.RAW,
            (
                SensorSeries(
                    sensor="dry_bulb",
                    node=Node.MAIN,
                    unit_family=UnitFamily.CELSIUS,
                    unit="C",
                    points=(
                        SeriesPoint(
                            timestamp=NOW,
                            average=24.0,
                            minimum=23.0,
                            maximum=25.0,
                            sample_count=3,
                        ),
                    ),
                ),
            ),
        )

    async def statistics(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[SensorStatistics, ...]:
        del room, monitoring_range
        return (
            SensorStatistics(
                sensor="dry_bulb",
                node=Node.MAIN,
                minimum=23.0,
                maximum=25.0,
                average=24.0,
                stddev_samp=1.0,
                sample_count=3,
            ),
        )

    async def live(self, room: str, node: str) -> tuple[()]:
        del room, node
        return ()


@final
class FakeControlReads:
    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        del location
        return ControlHistoryEnvelope(
            range=history_range,
            runtime_snapshot_version=1,
            requested_max_points=max_points,
            interval_seconds=derive_interval_seconds(
                history_range.end - history_range.start, 1, max_points
            ),
        )

    async def publications(self, location: str) -> ControlPublicationResponse:
        del location
        raise AssertionError("publication reads are outside this contract test")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def app():
    application = create_app(control_reads=FakeControlReads())
    application.dependency_overrides[get_sensor_reads] = FakeSensorReads
    return application


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "expected_metadata_key"),
    (
        ("/api/sensors/monitoring/range/Veg%20Room", "metadata"),
        ("/api/sensors/monitoring/stats/Veg%20Room", "metadata"),
        ("/api/monitoring/control/Veg%20Room/history", None),
    ),
)
async def test_max_points_boundaries_return_400_or_echo_budget(
    app, path: str, expected_metadata_key: str | None
) -> None:
    # Given: each read endpoint and its documented budget boundaries.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        legacy_response = await client.get(path)
        legacy_payload = legacy_response.json()
        legacy_metadata = (
            legacy_payload
            if expected_metadata_key is None
            else legacy_payload[expected_metadata_key]
        )
        assert legacy_response.status_code == 200
        assert legacy_metadata["requested_max_points"] is None
        assert legacy_metadata["interval_seconds"] is None

        for invalid in ("9", "100001", "abc", "10.5"):
            # When: a budget outside the integer 10..100000 contract is supplied.
            invalid_response = await client.get(path, params={"max_points": invalid})

            # Then: the API rejects it with the monitoring validation status.
            assert invalid_response.status_code == 400

        for accepted in (10, 100_000):
            # When: each inclusive valid edge is supplied.
            response = await client.get(path, params={"max_points": accepted})

            # Then: its requested budget is echoed without changing the read source.
            assert response.status_code == 200
            payload = response.json()
            metadata = payload if expected_metadata_key is None else payload[expected_metadata_key]
            assert metadata["requested_max_points"] == accepted
            assert metadata["interval_seconds"] is not None


@pytest.mark.anyio
async def test_no_budget_preserves_legacy_fields_and_returns_none_metadata(app) -> None:
    # Given: an old-client sensor range request without a point budget.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # When: the unchanged request is served.
        response = await client.get("/api/sensors/monitoring/range/Veg%20Room")

    # Then: legacy content is unchanged and only additive contract fields are null/defaulted.
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["requested_max_points"] is None
    assert payload["metadata"]["interval_seconds"] is None
    assert payload["series"][0]["point_count"] == 1
    assert payload["series"][0]["sample_count_total"] == 3
    assert payload["statistics"][0]["stddev_quality"] == "exact"


def test_interval_derivation_snaps_to_the_nice_ladder() -> None:
    # Given: a seven-day five-minute source range and a 1,000-point budget.
    monitoring_range = MonitoringRange(start=NOW - timedelta(days=7), end=NOW)

    # When: its contract-only effective interval is derived.
    interval_seconds = derive_interval_seconds(monitoring_range.duration, 300, 1000)

    # Then: the API echoes the applied 15-minute nice interval for exact bucketing.
    assert interval_seconds == 900


def test_legacy_models_parse_and_compute_additive_counts() -> None:
    # Given: an old serialized series/statistics fixture without new fields.
    serialized_point = {
        "timestamp": NOW.isoformat(),
        "average": 24.0,
        "minimum": 23.0,
        "maximum": 25.0,
        "sample_count": 3,
    }

    # When: the strict frozen response models parse the legacy-shaped objects.
    series = SensorSeries.model_validate_json(
        json.dumps(
            {
                "sensor": "dry_bulb",
                "node": "main",
                "unit_family": "celsius",
                "unit": "C",
                "points": [serialized_point],
            }
        )
    )
    statistics = SensorStatistics.model_validate_json(
        json.dumps(
            {
                "sensor": "dry_bulb",
                "node": "main",
                "minimum": 23.0,
                "maximum": 25.0,
                "average": 24.0,
                "stddev_samp": 1.0,
                "sample_count": 3,
            }
        )
    )

    # Then: defaults preserve parsing while response additions are computed/labelled.
    assert (series.point_count, series.sample_count_total) == (1, 3)
    assert statistics.stddev_quality == "exact"


@final
class StatisticsRowsDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        del query
        values = {
            "node": "main",
            "sensor": "dry_bulb",
            "minimum": 23.0,
            "maximum": 25.0,
            "average": 24.0,
            "stddev_samp": 1.0,
            "sample_count": 3,
        }
        row = MagicMock(spec=asyncpg.Record)
        row.__getitem__.side_effect = values.__getitem__
        return [row]


@final
class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


@pytest.mark.anyio
async def test_statistics_quality_tracks_the_existing_raw_or_cagg_branch() -> None:
    # Given: fake statistic rows supplied to the legacy raw and CAGG selections.
    repository = SensorMonitoringRepository(StatisticsRowsDatabase(), UnusedRedis())
    raw_range = MonitoringRange(start=NOW - timedelta(hours=1), end=NOW)
    cagg_range = MonitoringRange(start=NOW - timedelta(days=7), end=NOW)

    # When: both existing source-selection branches build response statistics.
    raw = await repository.statistics("Veg Room", raw_range)
    exact = await repository.statistics("Veg Room", cagg_range)

    # Then: the source's standard-deviation precision remains explicit on the wire.
    assert raw[0].stddev_quality == "exact"
    assert exact[0].stddev_quality == "exact"
