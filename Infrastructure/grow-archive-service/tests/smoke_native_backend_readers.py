"""Guarded real-DB proof of the backend repository's native history readers."""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sys
from uuid import uuid4

HERE = Path(__file__).resolve().parent
INFRASTRUCTURE = HERE.parent.parent
for _entry in (str(INFRASTRUCTURE / "backend"), str(INFRASTRUCTURE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import asyncpg
from fastapi import HTTPException
from app.repositories.sensor_registry_repository import SensorRegistryRepository
from app.repositories.sensor_repository import SensorRepository, _pick_aggregate_tier

# Backend and automation-service both ship a top-level ``app`` package. The
# backend repositories are bound above; release the name so the shared
# disposable-harness helpers can import the automation-service ``app``.
for _module in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
    del sys.modules[_module]
sys.path.remove(str(INFRASTRUCTURE / "backend"))

from smoke_derived_refresh import install_real_derived_schema  # noqa: E402
from smoke_lifecycle import _assert, _parse_database_url  # noqa: E402


BASE = datetime(2026, 4, 1, tzinfo=UTC)
SENSOR_ID = 1
SENSOR_NAME = "dry_bulb_b"
WIDTHS = {"1min": timedelta(minutes=1), "5min": timedelta(minutes=5),
          "hourly": timedelta(hours=1), "daily": timedelta(days=1)}


def _bucket(instant: datetime, width: timedelta) -> datetime:
    # Native views use UTC time_bucket with the epoch-aligned default origin.
    epoch = datetime(2000, 1, 3, tzinfo=UTC) if width >= timedelta(days=1) else datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + ((instant - epoch) // width) * width


def _expected(samples, tier: str, start: datetime, end: datetime) -> dict[datetime, float]:
    """Independent sample-weighted means for buckets starting within [start, end]."""
    if tier == "raw":
        return {t: v for t, v in samples if start <= t <= end}
    width = WIDTHS[tier]
    sums: dict[datetime, list[float]] = {}
    for instant, value in samples:
        bucket = _bucket(instant, width)
        if start <= bucket <= end:
            entry = sums.setdefault(bucket, [0.0, 0])
            entry[0] += value
            entry[1] += 1
    return {bucket: total / count for bucket, (total, count) in sums.items()}


async def _insert(conn: asyncpg.Connection, samples) -> None:
    await conn.executemany(
        "INSERT INTO measurement(time,sensor_id,value) VALUES($1,$2,$3::real)",
        [(instant, SENSOR_ID, value) for instant, value in samples],
    )


async def _assert_series(repo, samples, start, end, label) -> None:
    tier = _pick_aggregate_tier((end - start).total_seconds() / 3600).name
    result = await repo.get_sensor_data("Flower Room", "back", start, end)
    actual = {point.timestamp: point.value for point in result.get(SENSOR_NAME, [])}
    expected = _expected(samples, tier, start, end)
    _assert(set(actual) == set(expected),
            f"{label}/{tier}: bucket set differs; missing={sorted(set(expected)-set(actual))[:3]} "
            f"extra={sorted(set(actual)-set(expected))[:3]}")
    for bucket, value in expected.items():
        _assert(abs(actual[bucket] - value) <= 1e-6 * max(1.0, abs(value)),
                f"{label}/{tier}: {bucket} returned {actual[bucket]}, expected {value}")


async def _materialize(conn: asyncpg.Connection, lo: datetime, hi: datetime) -> None:
    for view in (
        "monitoring_measurement_1min", "monitoring_measurement_5min",
        "monitoring_measurement_1hour", "monitoring_measurement_1day",
    ):
        await conn.execute(
            "CALL public.refresh_continuous_aggregate($1::regclass,$2::timestamptz,$3::timestamptz,true)",
            f"public.{view}", lo, hi,
        )


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    if os.getenv("MONITORING_HARNESS_INTERNAL") != "1":
        raise RuntimeError("native backend reader proof requires the owned disposable harness")
    await install_real_derived_schema(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    dataset = uuid4()
    try:
        async with pool.acquire() as conn:
            _assert(await conn.fetchval(
                "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                "WHERE fixture_name='grow-data-lifecycle'"
            ) == 1, "missing owned grow lifecycle fixture marker")
            _assert(not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM measurement WHERE sensor_id=$1 AND time>=$2 AND time<$3)",
                SENSOR_ID, BASE - timedelta(days=1), BASE + timedelta(days=40),
            ), "fixture already owns the proof window")
            # Unequal sample counts per bucket expose any average-of-averages.
            samples = [
                (BASE + timedelta(minutes=2, seconds=1), 10.0),
                (BASE + timedelta(minutes=2, seconds=31), 20.0),
                (BASE + timedelta(minutes=2, seconds=59), 60.0),
                (BASE + timedelta(minutes=7), 30.0),
                (BASE + timedelta(hours=2, minutes=1), 90.0),
                (BASE + timedelta(hours=2, minutes=59), 110.0),
                (BASE + timedelta(hours=3, minutes=1), 130.0),
                (BASE + timedelta(days=2, minutes=1), 170.0),
                (BASE + timedelta(days=2, hours=5), 190.0),
                (BASE + timedelta(days=2, hours=5, minutes=1), 200.0),
                (BASE + timedelta(days=8, minutes=1), 210.0),
                (BASE + timedelta(days=8, hours=2), 250.0),
            ]
            await _insert(conn, samples)
            await _materialize(conn, BASE - timedelta(days=1), BASE + timedelta(days=31))

        repo = SensorRepository(pool=pool)
        ranges = (
            (BASE, BASE + timedelta(hours=2) - timedelta(microseconds=1), "raw"),
            (BASE, BASE + timedelta(hours=2), "1min"),
            (BASE, BASE + timedelta(hours=24), "5min"),
            (BASE, BASE + timedelta(days=7), "hourly"),
            (BASE, BASE + timedelta(days=30), "daily"),
        )
        for start, end, tier in ranges:
            _assert(_pick_aggregate_tier((end - start).total_seconds() / 3600).name == tier,
                    f"range {end - start} did not select {tier}")
            await _assert_series(repo, samples, start, end, "materialized")

        # Late correction inside already-materialized buckets plus a live tail
        # newer than every watermark: both must be served exactly, not dropped.
        late = [
            (BASE + timedelta(minutes=2, seconds=45), 1000.0),
            (BASE + timedelta(days=2, hours=5, minutes=30), -50.0),
            (BASE + timedelta(days=29, hours=1), 300.0),
            (BASE + timedelta(days=29, hours=1, minutes=2), 320.0),
        ]
        async with pool.acquire() as conn:
            await _insert(conn, late)
        everything = samples + late
        for start, end, _tier in ranges:
            await _assert_series(repo, everything, start, end, "unrefreshed")

        # Soil history shares the tier source: its aggregate SQL must run
        # against the real native schema and return exact weighted means.
        soil = SensorRegistryRepository(pool=pool)
        async with pool.acquire() as conn:
            sensor_rows = await conn.fetch(
                "SELECT sensor_id,name FROM sensor WHERE sensor_id=$1", SENSOR_ID)
            hourly = _pick_aggregate_tier(7 * 24)
            points = await soil._bucketed_points(
                conn, [{**dict(row), "name": row["name"] + "_temperature"} for row in sensor_rows],
                BASE, BASE + timedelta(days=7), hourly, 86400,
            )
        day_two = [value for instant, value in everything
                   if BASE + timedelta(days=2) <= instant < BASE + timedelta(days=3)]
        soil_points = {point.bucket_start: point for point in points.get("temperature", [])}
        second = soil_points.get(BASE + timedelta(days=2))
        _assert(second is not None and second.sample_count == len(day_two)
                and abs(second.average - sum(day_two) / len(day_two)) <= 1e-6 * abs(sum(day_two) / len(day_two))
                and second.minimum == min(day_two) and second.maximum == max(day_two),
                f"soil hourly tier lost exact daily statistics: {second}")

        # Removed grow/Veg history refuses ordinary reads; pending is served.
        removed_end = BASE + timedelta(minutes=30)
        expected_removed = sum(BASE <= instant < removed_end for instant, _value in everything)
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,calendar_year,quarter,room,available_from,available_to) "
                "VALUES($1,'veg_quarter',2099,1,'Flower Room',$2,$3)",
                dataset, BASE, removed_end,
            )
            removed = await conn.fetch(
                "DELETE FROM measurement WHERE sensor_id=$1 AND time>=$2 AND time<$3 RETURNING time",
                SENSOR_ID, BASE, removed_end,
            )
            _assert(len(removed) == expected_removed,
                    f"removed-history fixture deleted {len(removed)} rows, expected {expected_removed}")
            await conn.execute(
                "INSERT INTO archive_availability(dataset_id,revision,source_table,room,start_at,end_at,"
                "state,source_rows_removed,source_removed_at) "
                "VALUES($1,1,'measurement','Flower Room',$2,$3,'captured',true,clock_timestamp())",
                dataset, BASE, removed_end,
            )
        try:
            await repo.get_sensor_data("Flower Room", "back", BASE, removed_end)
        except HTTPException as exc:
            _assert(exc.status_code == 409 and exc.detail["code"] == "archive_selection_required",
                    f"removed history returned the wrong refusal: {exc.status_code} {exc.detail}")
        else:
            raise AssertionError("removed history was served by an ordinary repository read")
        pending_time = BASE + timedelta(minutes=25)
        pending_value = 42.125
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE archive_availability SET source_rows_removed=false,source_removed_at=NULL "
                "WHERE dataset_id=$1",
                dataset,
            )
            await conn.execute(
                "INSERT INTO measurement(time,sensor_id,value) VALUES($1,$2,$3::real)",
                pending_time, SENSOR_ID, pending_value,
            )
        pending = await repo.get_sensor_data("Flower Room", "back", BASE, removed_end)
        pending_points = pending.get(SENSOR_NAME, [])
        _assert(len(pending_points) == 1 and pending_points[0].timestamp == pending_time
                and pending_points[0].value == pending_value,
                f"pending history was not served from source rows: {pending_points}")
        print("native backend readers: raw/1min/5min/hourly/daily exact weighted series, "
              "late corrections and live tail served from raw, soil statistics exact, "
              "removed history refused, pending history served")
    finally:
        # The harness drops the owned database; no row cleanup is needed.
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    asyncio.run(run(parser.parse_args().database_url))
