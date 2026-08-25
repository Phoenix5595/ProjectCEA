from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from monitoring_service.sensor_models import (
    MonitoringRange,
    MonitoringUnavailableError,
    Node,
    SensorStatistics,
    StddevQuality,
    Tier,
)
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.cagg_watermarks import CaggWatermarkCache


class CoverageDatabase:
    def __init__(self, responses: list[list[asyncpg.Record]]) -> None:
        self._responses = responses
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *_: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.queries.append(query)
        return self._responses.pop(0)


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


class MonotonicClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class WatermarkDatabase:
    def __init__(self, watermarks: list[datetime]) -> None:
        self._watermarks = watermarks
        self.calls: list[tuple[str, tuple[str | int | float | datetime | timedelta, ...]]] = []

    async def fetch(
        self, query: str, *args: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.calls.append((query, args))
        if "materialization_watermark" in query:
            return [{"materialization_watermark": self._watermarks.pop(0)}]
        return []


class CaggStatisticsDatabase:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *_: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.queries.append(query)
        return [
            {
                "sensor": "dry_bulb",
                "node": "front",
                "minimum": 3,
                "maximum": 5,
                "average": 4,
                "stddev_samp": 2**0.5,
                "sample_count": 2,
            }
        ]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_cagg_coverage_rejects_an_absent_watermark() -> None:
    # Given: an aggregate read with no materialization watermark
    database = CoverageDatabase([[]])
    repository = SensorMonitoringRepository(database, UnusedRedis())
    end = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    monitoring_range = MonitoringRange(start=end - timedelta(hours=6), end=end)

    # When: the aggregate coverage guard runs
    with pytest.raises(MonitoringUnavailableError, match="CAGG is unavailable"):
        await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)

    # Then: it fails before any series query could be issued
    assert len(database.queries) == 1


def test_long_window_statistics_use_exact_sufficient_statistics() -> None:
    # Given: the CAGG statistics source
    from monitoring_service.sensor_sql import STATISTICS_CAGG_SQL

    # When: its standard deviation expression is inspected
    # Then: it derives sample variance from the aggregate sufficient statistics
    assert "sum(c.value_sum_squares)" in STATISTICS_CAGG_SQL
    assert "power(sum(c.value_sum), 2) / sum(c.sample_count)" in STATISTICS_CAGG_SQL
    assert "sum(c.sample_count) > 1" in STATISTICS_CAGG_SQL


@pytest.mark.anyio
async def test_long_window_statistics_serialize_the_exact_quality_label() -> None:
    # Given: a complete CAGG statistics result for a long monitoring window
    database = CaggStatisticsDatabase()
    repository = SensorMonitoringRepository(database, UnusedRedis())
    end = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    monitoring_range = MonitoringRange(start=end - timedelta(days=7), end=end)

    # When: the repository materializes its CAGG statistics response
    statistics = await repository.statistics("Flower Room", monitoring_range)

    # Then: sufficient-stat results are labelled exact in the serialized UI contract
    assert len(database.queries) == 1
    assert statistics[0].model_dump(mode="json")["stddev_quality"] == "exact"
    assert statistics[0].stddev_samp == pytest.approx(2**0.5, rel=1e-9)


@pytest.mark.anyio
async def test_cagg_watermark_cache_reuses_a_fresh_relation_entry() -> None:
    # Given: two same-tier coverage reads within the five-second TTL
    clock = MonotonicClock()
    watermark = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    database = WatermarkDatabase([watermark])
    cache = CaggWatermarkCache(frozenset({"monitoring_measurement_1min"}), clock=clock)
    repository = SensorMonitoringRepository(database, UnusedRedis(), watermark_cache=cache)
    monitoring_range = MonitoringRange(start=watermark - timedelta(hours=6), end=watermark)

    # When: the aggregate guard is evaluated twice
    first = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)
    second = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)

    # Then: the cached complete-tail bound avoids a second watermark query
    assert first == watermark - timedelta(minutes=1)
    assert second == first
    assert len(database.calls) == 1


@pytest.mark.anyio
async def test_cagg_watermark_cache_refetches_after_expiry_and_advances_the_bound() -> None:
    # Given: a cached watermark followed by a newer materialization after expiry
    clock = MonotonicClock()
    watermark = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    advanced = watermark + timedelta(minutes=1)
    database = WatermarkDatabase([watermark, advanced])
    cache = CaggWatermarkCache(frozenset({"monitoring_measurement_1min"}), clock=clock)
    repository = SensorMonitoringRepository(database, UnusedRedis(), watermark_cache=cache)
    monitoring_range = MonitoringRange(start=watermark - timedelta(hours=6), end=advanced)

    # When: the cache expires before the second coverage read
    first = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)
    clock.advance(5.1)
    second = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)

    # Then: the newer watermark replaces the old bound through a new database read
    assert first == watermark - timedelta(minutes=1)
    assert second == watermark
    assert len(database.calls) == 2


@pytest.mark.anyio
async def test_cagg_watermark_cache_isolated_by_relation_name() -> None:
    # Given: independent one- and five-minute CAGG watermarks
    clock = MonotonicClock()
    watermark = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    database = WatermarkDatabase([watermark, watermark])
    cache = CaggWatermarkCache(
        frozenset({"monitoring_measurement_1min", "monitoring_measurement_5min"}),
        clock=clock,
    )
    repository = SensorMonitoringRepository(database, UnusedRedis(), watermark_cache=cache)
    monitoring_range = MonitoringRange(start=watermark - timedelta(days=7), end=watermark)

    # When: each relation is checked once and the first relation is checked again
    _ = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)
    _ = await repository._require_cagg_coverage(Tier.FIVE_MINUTES, monitoring_range)
    _ = await repository._require_cagg_coverage(Tier.ONE_MINUTE, monitoring_range)

    # Then: each relation has one isolated cache entry
    assert len(database.calls) == 2


@pytest.mark.anyio
async def test_cagg_series_rejects_insufficient_coverage_before_querying_series() -> None:
    # Given: a watermark that does not extend beyond the requested start
    watermark = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    database = WatermarkDatabase([watermark])
    cache = CaggWatermarkCache(frozenset({"monitoring_measurement_1min"}), clock=MonotonicClock())
    repository = SensorMonitoringRepository(database, UnusedRedis(), watermark_cache=cache)
    monitoring_range = MonitoringRange(start=watermark, end=watermark + timedelta(hours=6))

    # When: a CAGG series request requires coverage
    with pytest.raises(MonitoringUnavailableError) as error:
        await repository.series("Flower Room", monitoring_range)

    # Then: it retains the 503 contract and never submits a series statement
    assert error.value.status_code == 503
    assert len(database.calls) == 1


@pytest.mark.anyio
async def test_tiered_series_binds_the_cached_complete_tail_bound() -> None:
    # Given: a one-minute CAGG with an incomplete trailing bucket at its watermark
    clock = MonotonicClock()
    watermark = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    database = WatermarkDatabase([watermark])
    cache = CaggWatermarkCache(frozenset({"monitoring_measurement_1min"}), clock=clock)
    repository = SensorMonitoringRepository(database, UnusedRedis(), watermark_cache=cache)
    monitoring_range = MonitoringRange(start=watermark - timedelta(hours=6), end=watermark)

    # When: aggregate series are queried
    _ = await repository.series("Flower Room", monitoring_range)

    # Then: every series SQL call receives the pre-watermark complete-tail bound
    series_calls = [
        call for call in database.calls if "FROM monitoring_measurement_1min" in call[0]
    ]
    assert series_calls
    assert all("$8" in query for query, _ in series_calls)
    assert all("SELECT max(bucket)" not in query for query, _ in series_calls)
    assert all(args[-1] == watermark - timedelta(minutes=1) for _, args in series_calls)


def test_statistics_rows_preserve_zero_stddev_for_fewer_than_two_samples() -> None:
    # Given: CAGG result rows whose sufficient statistics represent zero and one samples
    statistics = (
        SensorStatistics(
            sensor="empty",
            node=Node("front"),
            minimum=0,
            maximum=0,
            average=0,
            stddev_samp=0,
            sample_count=0,
        ),
        SensorStatistics(
            sensor="one",
            node=Node("front"),
            minimum=3,
            maximum=3,
            average=3,
            stddev_samp=0,
            sample_count=1,
        ),
    )

    # When: the rows are serialized through the statistics contract
    serialized = tuple(statistic.model_dump(mode="json") for statistic in statistics)

    # Then: the legacy nonnegative zero shape remains valid for n < 2
    assert [statistic["stddev_samp"] for statistic in serialized] == [0, 0]


def test_statistics_quality_serializes_exact_and_approximate_sources() -> None:
    # Given: identical CAGG-compatible statistics represented by each quality source
    # When: each source quality is materialized into the response contract
    exact = SensorStatistics(
        sensor="dry_bulb",
        node=Node("front"),
        minimum=3,
        maximum=5,
        average=4,
        stddev_samp=2**0.5,
        sample_count=2,
        stddev_quality=StddevQuality.EXACT,
    )
    approximate = SensorStatistics(
        sensor="dry_bulb",
        node=Node("front"),
        minimum=3,
        maximum=5,
        average=4,
        stddev_samp=2**0.5,
        sample_count=2,
        stddev_quality=StddevQuality.APPROXIMATE,
    )

    # Then: the UI-facing serialized response retains the source quality label
    assert exact.model_dump(mode="json")["stddev_quality"] == "exact"
    assert approximate.model_dump(mode="json")["stddev_quality"] == "approximate"
    assert exact.stddev_samp == pytest.approx(2**0.5, rel=1e-9)
