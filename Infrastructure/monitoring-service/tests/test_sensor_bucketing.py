from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import pytest

from monitoring_service.sensor_models import MonitoringRange, Tier, resolve_interval_seconds
from monitoring_service.sensor_repository import SensorMonitoringRepository


class SnapshotDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str | int | float | datetime | timedelta, ...]]] = []

    @staticmethod
    def _row(**values: int | float | str | datetime) -> asyncpg.Record:
        row = MagicMock(spec=asyncpg.Record)
        row.__getitem__.side_effect = values.__getitem__
        return row

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        self.calls.append((query, arguments))
        if "materialization_watermark" in query:
            return [self._row(materialization_watermark=datetime(2026, 8, 24, 14, tzinfo=UTC))]
        return [
            self._row(
                bucket=datetime(2026, 8, 24, 13, 0, tzinfo=UTC),
                sensor="dry_bulb",
                unit="°C",
                data_type="temperature",
                average=21.5,
                minimum=20.0,
                maximum=23.0,
                sample_count=4,
            ),
            self._row(
                bucket=datetime(2026, 8, 24, 13, 5, tzinfo=UTC),
                sensor="dry_bulb",
                unit="°C",
                data_type="temperature",
                average=22.5,
                minimum=21.0,
                maximum=24.0,
                sample_count=5,
            ),
        ]


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("duration_seconds", "max_points", "source_bucket_seconds", "expected"),
    (
        (604_800, 1_000, 300, 900),
        (3_600, 1_000, 1, 5),
        (3_600, 10, 1, 600),
        (60, 100_000, 60, 60),
    ),
)
def test_resolve_interval_seconds_snaps_budget_to_safe_ladder(
    duration_seconds: int, max_points: int, source_bucket_seconds: int, expected: int
) -> None:
    # Given: duration, budget, and source resolution combinations at ladder boundaries
    # When: a read-model interval is resolved
    # Then: only a safe ladder interval at least as coarse as both bounds is returned
    assert resolve_interval_seconds(duration_seconds, max_points, source_bucket_seconds) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("duration", "expected_tier", "expected_watermarks"),
    (
        (timedelta(hours=1), Tier.RAW, 0),
        (timedelta(hours=6), Tier.ONE_MINUTE, 1),
        (timedelta(days=7), Tier.FIVE_MINUTES, 1),
    ),
)
async def test_series_without_budget_preserves_characterized_snapshot(
    duration: timedelta, expected_tier: Tier, expected_watermarks: int
) -> None:
    # Given: representative legacy ranges and deterministic raw/CAGG read-model rows
    database = SnapshotDatabase()
    repository = SensorMonitoringRepository(database, UnusedRedis())
    end = datetime(2026, 8, 24, 14, tzinfo=UTC)
    monitoring_range = MonitoringRange(start=end - duration, end=end)

    # When: series is requested without the additive point budget
    tier, series = await repository.series("Flower Room", monitoring_range)

    # Then: tier, per-node points, values, and legacy query arity remain unchanged
    assert tier is expected_tier
    assert [item.model_dump(mode="json") for item in series] == [
        {
            "sensor": "dry_bulb",
            "node": "front",
            "unit_family": "celsius",
            "unit": "°C",
            "point_count": 2,
            "sample_count_total": 9,
            "points": [
                {
                    "timestamp": "2026-08-24T13:00:00Z",
                    "average": 21.5,
                    "minimum": 20.0,
                    "maximum": 23.0,
                    "sample_count": 4,
                },
                {
                    "timestamp": "2026-08-24T13:05:00Z",
                    "average": 22.5,
                    "minimum": 21.0,
                    "maximum": 24.0,
                    "sample_count": 5,
                },
            ],
        },
        {
            "sensor": "dry_bulb",
            "node": "back",
            "unit_family": "celsius",
            "unit": "°C",
            "point_count": 2,
            "sample_count_total": 9,
            "points": [
                {
                    "timestamp": "2026-08-24T13:00:00Z",
                    "average": 21.5,
                    "minimum": 20.0,
                    "maximum": 23.0,
                    "sample_count": 4,
                },
                {
                    "timestamp": "2026-08-24T13:05:00Z",
                    "average": 22.5,
                    "minimum": 21.0,
                    "maximum": 24.0,
                    "sample_count": 5,
                },
            ],
        },
    ]
    series_calls = [call for call in database.calls if "materialization_watermark" not in call[0]]
    assert len(series_calls) == 2
    assert all(len(arguments) == 4 for _, arguments in series_calls)
    assert (
        sum("materialization_watermark" in query for query, _ in database.calls)
        == expected_watermarks
    )
