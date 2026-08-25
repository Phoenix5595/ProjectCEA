from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import pytest

from monitoring_service.sensor_intervals import NICE_INTERVAL_SECONDS
from monitoring_service.sensor_models import MonitoringRange, resolve_interval_seconds
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_sql import RAW_BUCKETED_SERIES_SQL, TIERED_BUCKETED_STATEMENTS


def _row(**values: int | float | str | datetime) -> asyncpg.Record:
    values.setdefault("node", "main")
    row = MagicMock(spec=asyncpg.Record)
    row.__getitem__.side_effect = values.__getitem__
    return row


class EnvelopeDatabase:
    def __init__(self, rows: tuple[asyncpg.Record, ...], watermark: datetime) -> None:
        self.rows = rows
        self.watermark = watermark
        self.calls: list[tuple[str, tuple[str | int | float | datetime | timedelta, ...]]] = []

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.calls.append((query, arguments))
        if "materialization_watermark" in query:
            return [_row(materialization_watermark=self.watermark)]
        return list(self.rows)


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_raw_bucket_returns_the_exact_unit_weighted_envelope() -> None:
    # Given: uneven raw values whose first ten-second window has a known true envelope
    start = datetime(2026, 8, 24, 13, tzinfo=UTC)
    raw_window_values = (1.0, 9.0, 100.0)
    database = EnvelopeDatabase(
        (
            _row(
                bucket=start,
                sensor="dry_bulb",
                unit="°C",
                data_type="temperature",
                average=sum(raw_window_values) / len(raw_window_values),
                minimum=min(raw_window_values),
                maximum=max(raw_window_values),
                sample_count=len(raw_window_values),
            ),
            _row(
                bucket=start + timedelta(seconds=10),
                sensor="dry_bulb",
                unit="°C",
                data_type="temperature",
                average=25.0,
                minimum=20.0,
                maximum=30.0,
                sample_count=2,
            ),
        ),
        watermark=start,
    )
    monitoring_range = MonitoringRange(start=start, end=start + timedelta(seconds=100))

    # When: the raw series is read with a ten-point budget
    _, series = await SensorMonitoringRepository(database, UnusedRedis()).series(
        "Veg Room", monitoring_range, max_points=10
    )

    # Then: the SQL-bound result preserves the raw mean, extrema, and count exactly
    point = series[0].points[0]
    assert point.average == pytest.approx(110.0 / 3.0, abs=1e-9)
    assert (point.minimum, point.maximum, point.sample_count) == (1.0, 100.0, 3)
    query, arguments = database.calls[0]
    assert query == RAW_BUCKETED_SERIES_SQL
    assert arguments[7] == timedelta(seconds=10)
    assert "time_bucket($8::interval, measurement.time, $6::timestamptz)" in query


@pytest.mark.anyio
async def test_cagg_rebucket_uses_sum_weighted_mean_not_mean_of_means() -> None:
    # Given: CAGG sufficient statistics whose 10-to-1 weighting makes mean-of-means wrong
    start = datetime(2026, 8, 17, 13, tzinfo=UTC)
    cagg_means = (1.0, 100.0)
    cagg_counts = (10, 1)
    weighted_mean = 110.0 / 11.0
    database = EnvelopeDatabase(
        (
            _row(
                bucket=start,
                sensor="dry_bulb",
                unit="°C",
                data_type="temperature",
                average=weighted_mean,
                minimum=1.0,
                maximum=100.0,
                sample_count=sum(cagg_counts),
            ),
        ),
        watermark=start + timedelta(days=7),
    )
    monitoring_range = MonitoringRange(start=start, end=start + timedelta(days=7))

    # When: a five-minute source is rebucketed for a 1,000-point request
    _, series = await SensorMonitoringRepository(database, UnusedRedis()).series(
        "Veg Room", monitoring_range, max_points=1_000
    )

    # Then: summed sufficient statistics yield 110/11, not the skewed mean-of-means 50.5
    point = series[0].points[0]
    assert point.average == pytest.approx(weighted_mean, abs=1e-9)
    assert point.average != pytest.approx(sum(cagg_means) / len(cagg_means), abs=1e-9)
    assert (point.minimum, point.maximum, point.sample_count) == (1.0, 100.0, 11)
    query, arguments = database.calls[1]
    assert query == TIERED_BUCKETED_STATEMENTS["5min"]
    assert arguments[7] == timedelta(seconds=900)
    assert "sum(c.value_sum) / NULLIF(sum(c.sample_count), 0)" in query
    assert "avg(c.value_sum / c.sample_count)" not in query


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("duration", "max_points"),
    (
        *[(timedelta(seconds=1), budget) for budget in (None, 10, 100, 1_000, 100_000)],
        *[(timedelta(hours=1), budget) for budget in (None, 10, 100, 1_000, 100_000)],
        *[(timedelta(hours=1, seconds=1), budget) for budget in (None, 10, 100, 1_000, 100_000)],
        *[(timedelta(hours=6), budget) for budget in (None, 10, 100, 1_000, 100_000)],
        *[(timedelta(hours=6, seconds=1), budget) for budget in (None, 10, 100, 1_000, 100_000)],
        *[(timedelta(days=7), budget) for budget in (None, 10, 100, 1_000, 100_000)],
    ),
)
async def test_series_points_remain_ordered_unique_and_budget_bounded(
    duration: timedelta, max_points: int | None
) -> None:
    # Given: tier-boundary ranges and an ordered fake read-model envelope
    start = datetime(2026, 8, 17, 13, tzinfo=UTC)
    point_count = 17 if max_points is None else min(max_points, 17)
    rows = tuple(
        _row(
            bucket=start + timedelta(microseconds=index),
            sensor="dry_bulb",
            unit="°C",
            data_type="temperature",
            average=float(index),
            minimum=float(index),
            maximum=float(index),
            sample_count=1,
        )
        for index in range(point_count)
    )
    database = EnvelopeDatabase(rows, start + duration)
    monitoring_range = MonitoringRange(start=start, end=start + duration)

    # When: each supported budget is threaded through the source-selected series read
    _, series = await SensorMonitoringRepository(database, UnusedRedis()).series(
        "Veg Room", monitoring_range, max_points=max_points
    )

    # Then: the response remains an in-range ascending timeline with no duplicate buckets
    timestamps = tuple(point.timestamp for point in series[0].points)
    assert timestamps == tuple(sorted(timestamps))
    assert len(timestamps) == len(set(timestamps))
    assert all(
        monitoring_range.start <= timestamp < monitoring_range.end for timestamp in timestamps
    )
    if max_points is not None:
        assert len(timestamps) <= max_points


def test_resolved_intervals_are_ladder_whitelisted_and_never_sql_interpolated() -> None:
    # Given: arbitrary budget arithmetic including a source bucket between ladder values
    resolved = resolve_interval_seconds(604_800, 1_000, 301)

    # When: the repository-safe interval and its SQL statement are inspected
    # Then: resolution snaps to a whitelisted ladder value carried only as an interval parameter
    assert resolved in NICE_INTERVAL_SECONDS
    assert resolved == 900
    assert "$8::interval" in RAW_BUCKETED_SERIES_SQL
    assert str(resolved) not in RAW_BUCKETED_SERIES_SQL
