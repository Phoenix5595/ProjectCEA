from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest

from monitoring_service.sensor_models import MonitoringNotFoundError, MonitoringRange
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_routes import range_read


class SeriesRow(TypedDict):
    node: str
    sensor: str
    unit: str
    data_type: str
    bucket: datetime
    average: float
    minimum: float
    maximum: float
    sample_count: int


class StatisticsRow(TypedDict):
    node: str
    sensor: str
    minimum: float
    maximum: float
    average: float
    stddev_samp: float
    sample_count: int


class PatternRowsDatabase:
    def __init__(
        self,
        series_rows: dict[str, list[SeriesRow]],
        statistics_rows: dict[str, list[StatisticsRow]],
    ) -> None:
        self.series_rows: dict[str, list[SeriesRow]] = series_rows
        self.statistics_rows: dict[str, list[StatisticsRow]] = statistics_rows
        self.fetches: list[str] = []

    async def fetch(
        self, query: str, *args: str | int | float | datetime | timedelta
    ) -> Sequence[SeriesRow | StatisticsRow]:
        self.fetches.append(query)
        rows = self.statistics_rows if "stddev_samp" in query else self.series_rows
        if len(args) < 2:
            return []
        if len(args) >= 7:
            return [
                row for pattern in (str(args[2]), str(args[4])) for row in rows.get(pattern, [])
            ]
        return rows[str(args[1])]


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


def _range(hours: int = 1) -> MonitoringRange:
    end = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    return MonitoringRange(start=end - timedelta(hours=hours), end=end)


def _series_row(node: str, sensor: str, bucket: datetime) -> SeriesRow:
    return {
        "node": node,
        "sensor": sensor,
        "unit": "°C",
        "data_type": "temperature",
        "bucket": bucket,
        "average": 24.5,
        "minimum": 24.0,
        "maximum": 25.0,
        "sample_count": 2,
    }


def _statistics_row(node: str, sensor: str) -> StatisticsRow:
    return {
        "node": node,
        "sensor": sensor,
        "minimum": 24.0,
        "maximum": 25.0,
        "average": 24.5,
        "stddev_samp": 0.5,
        "sample_count": 2,
    }


def _database() -> PatternRowsDatabase:
    bucket = datetime(2026, 8, 24, 13, 30, tzinfo=UTC)
    return PatternRowsDatabase(
        {
            "%_f": [_series_row("front", "dry_bulb_f", bucket)],
            "%_b": [_series_row("back", "dry_bulb_b", bucket)],
            "%_other": [_series_row("front", "foreign_sensor", bucket)],
            "%_v": [_series_row("main", "dry_bulb_v", bucket)],
        },
        {
            "%_f": [_statistics_row("front", "dry_bulb_f")],
            "%_b": [_statistics_row("back", "dry_bulb_b")],
            "%_other": [_statistics_row("front", "foreign_sensor")],
            "%_v": [_statistics_row("main", "dry_bulb_v")],
        },
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_series_fetches_flower_nodes_once_and_excludes_foreign_pattern() -> None:
    # Given: a single SQL result set containing front and back sensor rows
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]

    # When: the Flower range is loaded
    _, series = await repository.series("Flower Room", _range())

    # Then: one fetch attributes only the supplied front/back rows
    assert len(database.fetches) == 1
    assert [(item.node.value, item.sensor) for item in series] == [
        ("front", "dry_bulb_f"),
        ("back", "dry_bulb_b"),
    ]


@pytest.mark.anyio
async def test_statistics_fetches_flower_nodes_once() -> None:
    # Given: a single SQL result set containing front and back statistics
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]

    # When: Flower statistics are loaded
    statistics = await repository.statistics("Flower Room", _range())

    # Then: one fetch attributes both rows to their canonical nodes
    assert len(database.fetches) == 1
    assert [(item.node.value, item.sensor) for item in statistics] == [
        ("front", "dry_bulb_f"),
        ("back", "dry_bulb_b"),
    ]


@pytest.mark.anyio
async def test_flower_range_uses_one_series_and_one_statistics_fetch() -> None:
    # Given: a Flower repository backed by a fetch-counting database fake
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]
    monitoring_range = _range()

    # When: the range route loads series and statistics for Flower
    response = await range_read(
        "Flower Room",
        start=monitoring_range.start.isoformat(),
        end=monitoring_range.end.isoformat(),
        max_points=None,
        reads=repository,
    )

    # Then: the route has one SQL fetch per read kind, not one per Flower node
    assert len(database.fetches) == 2
    assert len(response.series) == 2
    assert len(response.statistics) == 2


@pytest.mark.anyio
async def test_series_uses_main_for_veg() -> None:
    # Given: a canonical Veg row
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]

    # When: Veg is requested
    _, series = await repository.series("Veg Room", _range())

    # Then: Veg remains main-only after one fetch
    assert [(item.node.value, item.sensor) for item in series] == [("main", "dry_bulb_v")]
    assert len(database.fetches) == 1


@pytest.mark.anyio
async def test_series_never_fetches_unknown_room() -> None:
    # Given: an invalid room name
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]

    # When: the unknown room is requested
    with pytest.raises(MonitoringNotFoundError):
        _ = await repository.series("Unknown Room", _range())

    # Then: validation preserves the 404 path without a database fetch
    assert database.fetches == []


@pytest.mark.anyio
async def test_budgeted_series_remains_within_max_points_after_node_merge() -> None:
    # Given: two canonical Flower series with a point-budget request
    database = _database()
    repository = SensorMonitoringRepository(database, UnusedRedis())  # type: ignore[arg-type]

    # When: the range is fetched using the minimum valid budget
    _, series = await repository.series("Flower Room", _range(), max_points=10)

    # Then: each merged node/sensor series stays within the requested budget
    assert len(database.fetches) == 1
    assert all(item.point_count <= 10 for item in series)
