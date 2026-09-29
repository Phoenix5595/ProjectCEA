from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
import re
from urllib.parse import urlsplit

import asyncpg
import pytest

from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.sensor_models import MonitoringRange, Tier
from monitoring_service.sensor_repository import SensorMonitoringRepository


class UnusedRedis:
    async def sensor_values(self, _pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        return ()


def _disposable_database_url() -> str:
    database_url = os.environ.get("MONITORING_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("requires the guarded monitoring_test_* database harness")

    parsed = urlsplit(database_url)
    database_name = parsed.path.lstrip("/")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise AssertionError("sensor envelope integration test requires a loopback database")
    if re.fullmatch(r"monitoring_test_[a-z0-9_]+", database_name) is None:
        raise AssertionError(
            "sensor envelope integration test requires a monitoring_test_* database"
        )
    if "cea_sensors" in database_name:
        raise AssertionError("sensor envelope integration test refuses production database names")
    return database_url


async def _verify_two_line_envelope(database_url: str) -> None:
    start = datetime(2026, 9, 27, tzinfo=UTC)
    end = start + timedelta(seconds=10)
    rows: list[tuple[datetime, int, float]] = []
    for offset in range(10):
        dry_bulb = 42.0 if offset == 2 else 24.0
        humidity = 95.0 if offset == 2 else 60.0
        timestamp = start + timedelta(seconds=offset)
        rows.extend(((timestamp, 1, dry_bulb), (timestamp, 2, humidity)))

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as connection:
            await connection.executemany(
                "INSERT INTO measurement (time, sensor_id, value) VALUES ($1, $2, $3)",
                rows,
            )

        repository = SensorMonitoringRepository(ReadOnlyDatabase(pool), UnusedRedis())
        tier, series = await repository.series(
            "Flower Room", MonitoringRange(start=start, end=end), max_points=2
        )
    finally:
        await pool.close()

    assert tier is Tier.RAW
    assert {item.sensor for item in series} == {"dry_bulb_b", "rh_b"}
    expected_envelopes = {
        "dry_bulb_b": (24.0, 42.0, 27.6),
        "rh_b": (60.0, 95.0, 67.0),
    }
    for item in series:
        assert len(item.points) == 2
        first_bucket = item.points[0]
        minimum, maximum, average = expected_envelopes[item.sensor]
        assert first_bucket.sample_count == 5
        assert first_bucket.minimum == minimum
        assert first_bucket.maximum == maximum
        assert first_bucket.average == pytest.approx(average)
        assert first_bucket.average < first_bucket.maximum
        assert item.points[-1].timestamp == start + timedelta(seconds=5)


def test_two_line_raw_rebucket_preserves_narrow_spike_envelopes() -> None:
    asyncio.run(_verify_two_line_envelope(_disposable_database_url()))
