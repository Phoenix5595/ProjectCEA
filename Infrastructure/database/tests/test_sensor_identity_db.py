from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

import asyncpg
import psycopg2
import pytest

INFRASTRUCTURE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(INFRASTRUCTURE))
sys.path.insert(0, str(INFRASTRUCTURE / "backend"))

from app.repositories.sensor_registry_repository import SensorRegistryRepository  # noqa: E402
from app.sensor_registry_models import NewIdentityRequest, SameIdentityRequest  # noqa: E402


def _load_service_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load service module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


soil_database = _load_service_module(
    "sensor_identity_soil_database",
    INFRASTRUCTURE / "soil-sensor-service/app/database.py",
)
can_registry = _load_service_module(
    "sensor_identity_can_registry",
    INFRASTRUCTURE / "can-processor-service/app/sensor_registry.py",
)


@pytest.mark.asyncio
async def test_readdress_same_replacement_new_and_transient_absence() -> None:
    dsn = os.environ["MONITORING_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn)
    password = urlsplit(dsn).password or ""
    soil_db = soil_database.DatabaseManager(
        {
            "host": urlsplit(dsn).hostname or "127.0.0.1",
            "port": urlsplit(dsn).port or 5432,
            "database": (urlsplit(dsn).path or "/").lstrip("/"),
            "user": urlsplit(dsn).username or "cea_user",
            "password": unquote(password),
        }
    )
    soil_db._pool = pool
    repository = SensorRegistryRepository(pool=pool)
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
            original = await conn.fetchrow(
                """
                SELECT registry_id, device_id
                FROM sensor_registry
                WHERE bus = 'rs485' AND hardware_address = 226
                """
            )
            assert original is not None
            original_registry_id = int(original["registry_id"])
            _, original_sensors = await soil_db.register_sensor_device(
                original_registry_id, "soil_sensor_226"
            )
            await conn.execute(
                "UPDATE sensor_registry SET last_seen = clock_timestamp() - interval '11 minutes' "
                "WHERE registry_id = $1",
                original_registry_id,
            )

        # Re-addressing: stale 226 becomes the candidate for new address 233.
        pending = await soil_db.observe_probe(233)
        assert pending["identity_status"] == "pending"
        assert pending["pending_candidate_registry_id"] == original_registry_id
        _, pending_sensors = await soil_db.register_sensor_device(
            pending["registry_id"], "soil_sensor_233"
        )
        observed_at = datetime.now(UTC)
        pending_sensor_id = pending_sensors["temperature"]
        original_sensor_id = original_sensors["temperature"]
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO measurement (time, sensor_id, value) VALUES ($1, $2, $3)",
                observed_at,
                pending_sensor_id,
                21.5,
            )

        await repository.resolve_identity(
            pending["registry_id"],
            SameIdentityRequest(decision="same", target_registry_id=original_registry_id),
        )
        async with pool.acquire() as conn:
            target = await conn.fetchrow(
                "SELECT hardware_address, identity_status FROM sensor_registry "
                "WHERE registry_id = $1",
                original_registry_id,
            )
            assert target is not None
            assert target["hardware_address"] == 233
            assert target["identity_status"] == "confirmed"
            moved = await conn.fetchrow(
                "SELECT sensor_id, value FROM measurement WHERE time = $1",
                observed_at,
            )
            assert moved is not None
            assert moved["sensor_id"] == original_sensor_id
            assert float(moved["value"]) == 21.5
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM sensor_registry WHERE registry_id = $1",
                    pending["registry_id"],
                )
                == 0
            )

        # Replacement: a stale confirmed CAN address reappears, then the
        # operator keeps the new device separate with the `new` decision.
        async with pool.acquire() as conn:
            can_original = await conn.fetchrow(
                """
                SELECT registry_id, device_id
                FROM sensor_registry
                WHERE bus = 'can' AND hardware_address = 1
                """
            )
            assert can_original is not None
            can_original_id = int(can_original["registry_id"])
            can_original_device = int(can_original["device_id"])
            await conn.execute(
                "UPDATE sensor_registry SET last_seen = clock_timestamp() - interval '11 minutes' "
                "WHERE registry_id = $1",
                can_original_id,
            )

        connection = psycopg2.connect(dsn)
        try:
            worker = can_registry.MetadataWorker(cache=can_registry.AssignmentCache(), db_config={})
            assert worker._upsert_node(connection, 1)
        finally:
            connection.close()

        async with pool.acquire() as conn:
            replacement = await conn.fetchrow(
                """
                SELECT registry_id, device_id, identity_status, pending_candidate_registry_id
                FROM sensor_registry
                WHERE bus = 'can' AND hardware_address = 1
                """
            )
            assert replacement is not None
            replacement_id = int(replacement["registry_id"])
            assert replacement["identity_status"] == "pending"
            assert replacement["pending_candidate_registry_id"] == can_original_id
            replacement_device = int(replacement["device_id"])

        await repository.resolve_identity(replacement_id, NewIdentityRequest(decision="new"))
        async with pool.acquire() as conn:
            detached = await conn.fetchrow(
                "SELECT hardware_address, identity_status FROM sensor_registry "
                "WHERE registry_id = $1",
                can_original_id,
            )
            confirmed_new = await conn.fetchrow(
                "SELECT hardware_address, device_id, identity_status, pending_since "
                "FROM sensor_registry WHERE registry_id = $1",
                replacement_id,
            )
            assert detached is not None
            assert detached["hardware_address"] is None
            assert detached["identity_status"] == "detached"
            assert confirmed_new is not None
            assert confirmed_new["identity_status"] == "confirmed"
            assert confirmed_new["pending_since"] is None
            assert confirmed_new["hardware_address"] == 1
            assert int(confirmed_new["device_id"]) != can_original_device
            assert int(confirmed_new["device_id"]) == replacement_device

        # A 30-second interruption is below ABSENCE and keeps the same row.
        async with pool.acquire() as conn:
            transient = await conn.fetchrow(
                "SELECT registry_id FROM sensor_registry "
                "WHERE bus = 'rs485' AND hardware_address = 228"
            )
            assert transient is not None
            transient_id = int(transient["registry_id"])
            await conn.execute(
                "UPDATE sensor_registry SET last_seen = clock_timestamp() - interval '30 seconds' "
                "WHERE registry_id = $1",
                transient_id,
            )

        observed = await soil_db.observe_probe(228)
        assert observed["registry_id"] == transient_id
        assert observed["identity_status"] == "confirmed"
        assert observed["pending_since"] is None
        async with pool.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM sensor_registry WHERE bus = 'rs485' "
                    "AND identity_status = 'pending'"
                )
                == 0
            )
    finally:
        await pool.close()
