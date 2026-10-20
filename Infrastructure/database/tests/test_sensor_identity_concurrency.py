from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib.util
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

import asyncpg
import pytest

INFRASTRUCTURE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRASTRUCTURE))
sys.path.insert(0, str(INFRASTRUCTURE / "backend"))

from app.repositories.sensor_registry_repository import SensorRegistryRepository  # noqa: E402
from app.sensor_registry_models import SameIdentityRequest  # noqa: E402


def _load_service_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load service module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


soil_database = _load_service_module(
    "sensor_identity_concurrency_soil_database",
    INFRASTRUCTURE / "soil-sensor-service/app/database.py",
)


@pytest.mark.asyncio
async def test_same_merge_serializes_continuous_measurement_ingestion() -> None:
    dsn = os.environ["MONITORING_TEST_DATABASE_URL"]
    assert urlsplit(dsn).port == 5433
    pool = await asyncpg.create_pool(dsn=dsn)
    password = unquote(urlsplit(dsn).password or "")
    soil_db = soil_database.DatabaseManager(
        {
            "host": urlsplit(dsn).hostname or "127.0.0.1",
            "port": urlsplit(dsn).port or 5433,
            "database": (urlsplit(dsn).path or "/").lstrip("/"),
            "user": urlsplit(dsn).username or "cea_user",
            "password": password,
        }
    )
    soil_db._pool = pool
    repository = SensorRegistryRepository(pool=pool)
    initial_write: asyncio.Task[object] | None = None
    merge: asyncio.Task[object] | None = None
    continuous_writer: asyncio.Task[None] | None = None
    first_write_release = asyncio.Event()
    merge_release = asyncio.Event()
    shared_lock_acquired = asyncio.Event()
    merge_has_exclusive_locks = asyncio.Event()

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public.archive_availability (
                    source_table TEXT NOT NULL,
                    start_at TIMESTAMPTZ NOT NULL,
                    end_at TIMESTAMPTZ NOT NULL,
                    source_rows_removed BOOLEAN NOT NULL
                )
                """
            )
            await conn.execute("UPDATE sensor_registry SET last_seen = clock_timestamp()")
            now = await conn.fetchval("SELECT clock_timestamp()")
            target_device_id = await conn.fetchval(
                """
                INSERT INTO device (rack_id, name, type, serial_number)
                VALUES (NULL, 'Soil Sensor - identity lock target',
                        'RS485 Soil Sensor', 'MODBUS-701')
                RETURNING device_id
                """
            )
            target_registry_id = await conn.fetchval(
                """
                INSERT INTO sensor_registry (
                    bus, hardware_address, device_id, display_name,
                    identity_status, first_seen, last_seen
                )
                VALUES (
                    'rs485', NULL, $1, 'soil_sensor_701', 'detached',
                    $2::timestamptz - interval '1 hour',
                    $2::timestamptz - interval '11 minutes'
                )
                RETURNING registry_id
                """,
                target_device_id,
                now,
            )
            await conn.execute(
                """
                INSERT INTO sensor_address_history (
                    registry_id, bus, hardware_address, valid_from, valid_to
                ) VALUES (
                    $1, 'rs485', 701,
                    $2::timestamptz - interval '1 hour',
                    $2::timestamptz - interval '11 minutes'
                )
                """,
                target_registry_id,
                now,
            )
            target_sensor_id = await conn.fetchval(
                """
                INSERT INTO sensor (device_id, name, unit, data_type)
                VALUES ($1, 'soil_sensor_701_temperature', '°C', 'temperature')
                RETURNING sensor_id
                """,
                target_device_id,
            )

        pending = await soil_db.observe_probe(702)
        assert pending["identity_status"] == "pending"
        assert pending["pending_candidate_registry_id"] == target_registry_id
        await soil_db.register_sensor_device(
            int(pending["registry_id"]), 702, "soil_sensor_702"
        )
        first_sample = await pool.fetchval("SELECT clock_timestamp()")

        original_ensure_channels = soil_db._ensure_soil_channels
        pause_first_write = True

        async def pause_first_channel_resolution(
            conn: asyncpg.Connection, device_id: int, probe_name: str
        ):
            nonlocal pause_first_write
            if pause_first_write:
                pause_first_write = False
                shared_lock_acquired.set()
                await first_write_release.wait()
            return await original_ensure_channels(conn, device_id, probe_name)

        soil_db._ensure_soil_channels = pause_first_channel_resolution
        original_channel_pairs = repository._identity_channel_pairs

        async def pause_merge_after_locking(*args):
            merge_has_exclusive_locks.set()
            await merge_release.wait()
            return await original_channel_pairs(*args)

        repository._identity_channel_pairs = pause_merge_after_locking
        initial_write = asyncio.create_task(
            soil_db.store_measurements(
                int(pending["registry_id"]),
                702,
                "soil_sensor_702",
                {"temperature": 10_000.0},
                first_sample,
            )
        )
        await asyncio.wait_for(shared_lock_acquired.wait(), timeout=5)
        merge = asyncio.create_task(
            repository.resolve_identity(
                int(pending["registry_id"]),
                SameIdentityRequest(
                    decision="same", target_registry_id=int(target_registry_id)
                ),
            )
        )
        await asyncio.sleep(0.05)
        assert not merge.done(), "merge must wait for an in-flight shared-lock writer"
        first_write_release.set()
        assert await initial_write is not None
        await asyncio.wait_for(merge_has_exclusive_locks.wait(), timeout=5)

        async def keep_writing() -> None:
            for index in range(1, 41):
                stored = await soil_db.store_measurements(
                    int(pending["registry_id"]),
                    702,
                    "soil_sensor_702",
                    {"temperature": 10_000.0 + index},
                    first_sample + timedelta(microseconds=index),
                )
                assert stored is not None

        continuous_writer = asyncio.create_task(keep_writing())
        await asyncio.sleep(0.05)
        assert not continuous_writer.done(), "ingestion must wait while the merge holds its locks"
        merge_release.set()
        await merge
        await continuous_writer

        async with pool.acquire() as conn:
            moved = await conn.fetch(
                """
                SELECT sensor_id, value
                FROM measurement
                WHERE time >= $1 AND value >= 10000
                ORDER BY time
                """,
                first_sample,
            )
            assert len(moved) == 41
            assert {int(row["sensor_id"]) for row in moved} == {int(target_sensor_id)}
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM sensor_registry WHERE registry_id = $1",
                    pending["registry_id"],
                )
                == 0
            )
    finally:
        first_write_release.set()
        merge_release.set()
        pending_tasks = [
            task
            for task in (initial_write, merge, continuous_writer)
            if task is not None and not task.done()
        ]
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await pool.close()
