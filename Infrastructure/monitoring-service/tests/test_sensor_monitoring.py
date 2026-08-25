from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from monitoring_service.sensor_models import Tier, compute_tier
from monitoring_service.sensor_repository import SensorMonitoringRepository


class QueryCapturingDatabase:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *_: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.queries.append(query)
        return []


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


def test_compute_tier_preserves_legacy_duration_boundaries() -> None:
    # Given: monitoring durations at each captured legacy tier boundary
    raw_duration = timedelta(hours=1)
    one_minute_duration = timedelta(hours=6)
    five_minute_duration = timedelta(days=7)

    # When: each duration is resolved for a monitoring read
    tiers = (
        compute_tier(raw_duration),
        compute_tier(one_minute_duration),
        compute_tier(five_minute_duration),
    )

    # Then: the established raw, 1-minute, and 5-minute selections remain stable
    assert tiers == (Tier.RAW, Tier.ONE_MINUTE, Tier.FIVE_MINUTES)


def test_tiered_series_sql_reads_only_its_own_cagg() -> None:
    # Given: the SQL selected for each aggregated tier
    # When: the source relations are inspected
    # Then: each statement reads exactly its own CAGG (watermark-guarded)
    from monitoring_service.sensor_repository import _TIERED_STATEMENTS

    assert "_materialized" not in _TIERED_STATEMENTS["1min"]
    assert "monitoring_measurement_1min" in _TIERED_STATEMENTS["1min"]
    assert "monitoring_measurement_5min" in _TIERED_STATEMENTS["5min"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_live_drops_readings_older_than_staleness_cutoff() -> None:
    """Disconnected sensors must not haunt the LIVE panel with months-old last-goods."""

    class StubRedis:
        async def sensor_values(self, pattern: str):
            del pattern
            now_ms = int(datetime.now(UTC).timestamp() * 1000)
            return (
                ("cea:sensor:Flower Room:back:dry_bulb_b", "24.28", str(now_ms)),
                ("cea:sensor:Flower Room:back:wet_bulb_f", "16.71", "631152000000"),
            )

    class StubDatabase:
        async def fetch(
            self, query: str, *_: str | int | float | datetime | timedelta
        ) -> list[asyncpg.Record]:
            del query
            return []

    repository = SensorMonitoringRepository(StubDatabase(), StubRedis())  # type: ignore[arg-type]
    values = await repository.live("Flower Room", "back")

    names = [v.sensor for v in values]
    assert names == ["dry_bulb_b"]


def test_compute_tier_accepts_live_tick_windows() -> None:
    """The ~2s live-refresh window must resolve to the raw tier, not a validation error."""
    from monitoring_service.sensor_models import MonitoringRange

    end = datetime(2026, 8, 24, 14, 0, 2, tzinfo=UTC)
    start = datetime(2026, 8, 24, 14, 0, 0, tzinfo=UTC)
    rng = MonitoringRange(start=start, end=end)
    assert compute_tier(rng.duration) == Tier.RAW


@pytest.mark.anyio
async def test_statistics_uses_five_minute_cagg_when_duration_exceeds_48_hours() -> None:
    # Given: a seven-day statistics request and a query-capturing database fake
    from monitoring_service.sensor_models import MonitoringRange

    database = QueryCapturingDatabase()
    repository = SensorMonitoringRepository(database, UnusedRedis())
    end = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    monitoring_range = MonitoringRange(start=end - timedelta(days=7), end=end)

    # When: statistics are loaded for the wide request
    _ = await repository.statistics("Flower Room", monitoring_range)

    # Then: every sensor query reads only the five-minute aggregate
    assert database.queries
    assert all("monitoring_measurement_5min" in query for query in database.queries)
    assert all("FROM measurement " not in query for query in database.queries)


@pytest.mark.anyio
async def test_statistics_uses_raw_measurements_when_duration_is_at_most_48_hours() -> None:
    # Given: a six-hour statistics request and a query-capturing database fake
    from monitoring_service.sensor_models import MonitoringRange

    database = QueryCapturingDatabase()
    repository = SensorMonitoringRepository(database, UnusedRedis())
    end = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    monitoring_range = MonitoringRange(start=end - timedelta(hours=6), end=end)

    # When: statistics are loaded for the short request
    _ = await repository.statistics("Flower Room", monitoring_range)

    # Then: every sensor query retains the exact raw-measurement statement
    assert database.queries
    assert all("FROM measurement " in query for query in database.queries)
