"""R9 soil placement semantics on a disposable baseline + 0013 database.

Run only by ``test-soil-placement.sh`` (``MONITORING_TEST_DATABASE_URL`` on
port 5433). Exercises the soil service's identity/placement SQL and the
backend placement and heatmap SQL against the real schema.
"""

from __future__ import annotations

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
from app.sensor_registry_models import Rs485AssignmentRequest  # noqa: E402


def _load_service_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load service module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


soil_database = _load_service_module(
    "soil_placement_soil_database",
    INFRASTRUCTURE / "soil-sensor-service/app/database.py",
)


def _flower(bed: str, x: float, y: float) -> Rs485AssignmentRequest:
    return Rs485AssignmentRequest(kind="rs485", room="Flower Room", bed=bed, x_frac=x, y_frac=y)


VEG = Rs485AssignmentRequest(kind="rs485", room="Veg Room")


async def _placements(conn: asyncpg.Connection, registry_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT room_id, rack_id, x_frac::float AS x, y_frac::float AS y,
               placed_at, removed_at, closed_reason
        FROM soil_probe_placement WHERE registry_id = $1
        ORDER BY placed_at, placement_id
        """,
        registry_id,
    )


async def _registry(conn: asyncpg.Connection, registry_id: int) -> asyncpg.Record:
    return await conn.fetchrow(
        """
        SELECT sr.room_id, sr.rack_id, sr.device_id, d.rack_id AS device_rack_id
        FROM sensor_registry sr LEFT JOIN device d ON d.device_id = sr.device_id
        WHERE sr.registry_id = $1
        """,
        registry_id,
    )


async def _go_silent(conn: asyncpg.Connection, registry_id: int) -> None:
    """Placement opened 30 min ago, last answer 11 min ago (older placements shifted back)."""
    await conn.execute(
        "UPDATE soil_probe_placement SET placed_at = placed_at - interval '2 hours', "
        "removed_at = removed_at - interval '2 hours' "
        "WHERE registry_id = $1 AND removed_at IS NOT NULL",
        registry_id,
    )
    await conn.execute(
        "UPDATE soil_probe_placement SET placed_at = clock_timestamp() - interval '30 minutes' "
        "WHERE registry_id = $1 AND removed_at IS NULL",
        registry_id,
    )
    await conn.execute(
        "UPDATE sensor_registry SET last_seen = clock_timestamp() - interval '11 minutes' "
        "WHERE registry_id = $1",
        registry_id,
    )


@pytest.mark.asyncio
async def test_identity_placement_reconnect_and_heatmap() -> None:
    dsn = os.environ["MONITORING_TEST_DATABASE_URL"]
    parts = urlsplit(dsn)
    assert parts.port == 5433
    assert (parts.path or "/").lstrip("/").startswith("monitoring_test_")
    pool = await asyncpg.create_pool(dsn=dsn)
    soil_db = soil_database.DatabaseManager(
        {
            "host": parts.hostname or "127.0.0.1",
            "port": parts.port,
            "database": (parts.path or "/").lstrip("/"),
            "user": parts.username or "cea_user",
            "password": unquote(parts.password or ""),
        }
    )
    soil_db._pool = pool
    repository = SensorRegistryRepository(pool=pool)
    try:
        async with pool.acquire() as conn:
            back = await conn.fetchval("SELECT rack_id FROM rack WHERE name = 'Back Bed'")
            front = await conn.fetchval("SELECT rack_id FROM rack WHERE name = 'Front Bed'")
            veg = await conn.fetchval("SELECT room_id FROM room WHERE name = 'Veg Room'")
            # Keep the baseline probes (registry 4/5) out of the absence sweep.
            await conn.execute("UPDATE sensor_registry SET last_seen = clock_timestamp()")

        # adopt_probe: confirmed row, history row, device, four channels, no placement.
        entry = await soil_db.adopt_probe(7)
        reg7 = entry["registry_id"]
        assert entry["hardware_address"] == 7
        assert (entry["bed_name"], entry["room_name"]) == (None, None)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT identity_status, device_id FROM sensor_registry WHERE registry_id = $1",
                reg7,
            )
            assert row["identity_status"] == "confirmed"
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM sensor_address_history "
                    "WHERE registry_id = $1 AND bus = 'rs485' AND hardware_address = 7",
                    reg7,
                )
                == 1
            )
            assert (
                await conn.fetchval(
                    "SELECT name FROM device WHERE device_id = $1", row["device_id"]
                )
                == "soil_sensor_7"
            )
            channels = await conn.fetch(
                "SELECT name FROM sensor WHERE device_id = $1 ORDER BY name", row["device_id"]
            )
            assert [r["name"] for r in channels] == [
                "soil_sensor_7_ec",
                "soil_sensor_7_humidity",
                "soil_sensor_7_ph",
                "soil_sensor_7_temperature",
            ]
            assert await _placements(conn, reg7) == []
            humidity_sensor = await conn.fetchval(
                "SELECT sensor_id FROM sensor WHERE device_id = $1 AND name = 'soil_sensor_7_humidity'",
                row["device_id"],
            )

        # Place in Back Bed, move to Front Bed: one shared timestamp.
        record = await repository.assign(reg7, _flower("Back Bed", 0.25, 0.75))
        assert record.assignment is not None
        assert record.assignment.model_dump() == {
            "kind": "rs485",
            "room": "Flower Room",
            "bed": "Back Bed",
            "x_frac": 0.25,
            "y_frac": 0.75,
        }
        async with pool.acquire() as conn:
            (opened,) = await _placements(conn, reg7)
            assert (opened["rack_id"], opened["x"], opened["y"], opened["removed_at"]) == (
                back,
                0.25,
                0.75,
                None,
            )
            reg = await _registry(conn, reg7)
            assert (reg["rack_id"], reg["room_id"], reg["device_rack_id"]) == (back, None, back)

        await repository.assign(reg7, _flower("Front Bed", 0.5, 0.5))
        async with pool.acquire() as conn:
            first, second = await _placements(conn, reg7)
            assert first["closed_reason"] == "moved"
            assert first["removed_at"] == second["placed_at"]
            assert second["rack_id"] == front

        # Veg is room-level and passes the new legal-state branch.
        record = await repository.assign(reg7, VEG)
        assert record.assignment is not None and record.assignment.room == "Veg Room"
        async with pool.acquire() as conn:
            reg = await _registry(conn, reg7)
            assert (reg["room_id"], reg["rack_id"], reg["device_rack_id"]) == (veg, None, None)

        # Unassign closes the placement and clears the mirror.
        record = await repository.unassign(reg7)
        assert record.status == "unassigned"
        async with pool.acquire() as conn:
            assert (await _placements(conn, reg7))[-1]["closed_reason"] == "unassigned"
            reg = await _registry(conn, reg7)
            assert (reg["room_id"], reg["rack_id"]) == (None, None)

        # An 'unassigned' closure is never restored on reconnect.
        observed = await soil_db.observe_known_probe(7)
        assert (observed["bed_name"], observed["room_name"]) == (None, None)

        # Disconnect closes with removed_at = last_seen; reconnect restores the spot.
        await repository.assign(reg7, _flower("Back Bed", 0.25, 0.75))
        async with pool.acquire() as conn:
            await _go_silent(conn, reg7)
            last_seen = await conn.fetchval(
                "SELECT last_seen FROM sensor_registry WHERE registry_id = $1", reg7
            )
        closed = await soil_db.close_absent_placements()
        assert closed == [reg7]
        async with pool.acquire() as conn:
            last = (await _placements(conn, reg7))[-1]
            assert (last["closed_reason"], last["removed_at"]) == ("disconnected", last_seen)
            reg = await _registry(conn, reg7)
            assert (reg["rack_id"], reg["room_id"], reg["device_rack_id"]) == (None, None, None)

        observed = await soil_db.observe_known_probe(7)
        assert (observed["bed_name"], observed["room_name"]) == ("Back Bed", "Flower Room")
        async with pool.acquire() as conn:
            last = (await _placements(conn, reg7))[-1]
            assert (last["rack_id"], last["x"], last["y"], last["removed_at"]) == (
                back,
                0.25,
                0.75,
                None,
            )
            reg = await _registry(conn, reg7)
            assert (reg["rack_id"], reg["device_rack_id"]) == (back, back)

        # Spot taken: another probe within 1 ft (0.28 ft away) keeps 7 unassigned.
        other = await soil_db.adopt_probe(8)
        reg8 = other["registry_id"]
        async with pool.acquire() as conn:
            await _go_silent(conn, reg7)
        assert reg7 in await soil_db.close_absent_placements()
        await repository.assign(reg8, _flower("Back Bed", 0.30, 0.80))
        observed = await soil_db.observe_known_probe(7)
        assert (observed["bed_name"], observed["room_name"]) == (None, None)
        async with pool.acquire() as conn:
            assert (await _placements(conn, reg7))[-1]["closed_reason"] == "disconnected"

        # A disconnected Veg probe returns to Veg.
        veg_probe = await soil_db.adopt_probe(9)
        reg9 = veg_probe["registry_id"]
        await repository.assign(reg9, VEG)
        async with pool.acquire() as conn:
            await _go_silent(conn, reg9)
        assert reg9 in await soil_db.close_absent_placements()
        observed = await soil_db.observe_known_probe(9)
        assert (observed["bed_name"], observed["room_name"]) == (None, "Veg Room")
        async with pool.acquire() as conn:
            reg = await _registry(conn, reg9)
            assert (reg["room_id"], reg["rack_id"]) == (veg, None)

        # Heatmap: probe 8 active in Back Bed; synthetic placements for 7.
        async with pool.acquire() as conn:
            sensor8 = await conn.fetchval(
                "SELECT s.sensor_id FROM sensor s JOIN sensor_registry sr ON sr.device_id = s.device_id "
                "WHERE sr.registry_id = $1 AND s.name LIKE '%\\_humidity'",
                reg8,
            )
            # Active placement: only samples inside the last 10 minutes count.
            await conn.execute(
                "UPDATE soil_probe_placement SET placed_at = clock_timestamp() - interval '1 hour' "
                "WHERE registry_id = $1 AND removed_at IS NULL",
                reg8,
            )
            await conn.executemany(
                "INSERT INTO measurement (time, sensor_id, value) "
                "VALUES (clock_timestamp() - $1::interval, $2, $3)",
                [
                    (timedelta(minutes=30), sensor8, 90.0),
                    (timedelta(minutes=2), sensor8, 30.0),
                    (timedelta(minutes=1), sensor8, 40.0),
                ],
            )
            # Closed 2 days ago, open for an hour before that: pre-removal mean.
            await conn.execute(
                """
                INSERT INTO soil_probe_placement
                    (registry_id, room_id, rack_id, x_frac, y_frac, placed_at, removed_at, closed_reason)
                VALUES ($1, 1, $2, 0.9, 0.1, clock_timestamp() - interval '49 hours',
                        clock_timestamp() - interval '48 hours', 'moved'),
                       ($1, 1, $2, 0.1, 0.9, clock_timestamp() - interval '97 hours',
                        clock_timestamp() - interval '96 hours', 'moved')
                """,
                reg7,
                back,
            )
            await conn.executemany(
                "INSERT INTO measurement (time, sensor_id, value) "
                "VALUES (clock_timestamp() - $1::interval, $2, $3)",
                [
                    (timedelta(hours=48, minutes=5), humidity_sensor, 20.0),
                    (timedelta(hours=48, minutes=3), humidity_sensor, 24.0),
                    (timedelta(hours=96, minutes=2), humidity_sensor, 50.0),
                ],
            )

        heatmap = await repository.soil_heatmap("water_content")
        assert heatmap.metric == "water_content"
        assert heatmap.stale_after_hours == 72
        assert [bed.name for bed in heatmap.beds] == ["Back Bed", "Front Bed"]
        assert (heatmap.beds[0].width_ft, heatmap.beds[0].length_ft) == (4.0, 4.0)
        points = {(p.registry_id, p.x_frac, p.y_frac): p for p in heatmap.beds[0].points}
        active = points[(reg8, 0.3, 0.8)]
        assert active.active and active.removed_at is None
        assert active.value == pytest.approx(35.0)
        closed_point = points[(reg7, 0.9, 0.1)]
        assert not closed_point.active
        assert closed_point.value == pytest.approx(22.0)
        assert (reg7, 0.1, 0.9) not in points  # 4 days old
        assert (reg7, 0.25, 0.75) not in points  # no measurements in its window
        assert all(p.registry_id not in (4, 5) for bed in heatmap.beds for p in bed.points)

        # Addresses ever used stay reserved, including history-only ones.
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sensor_address_history (registry_id, bus, hardware_address, valid_from, valid_to) "
                "VALUES ($1, 'rs485', 11, clock_timestamp() - interval '1 day', clock_timestamp())",
                reg9,
            )
        reserved = await soil_db.reserved_addresses()
        assert {1, 2, 7, 8, 9, 11} <= reserved
        assert 10 not in reserved
    finally:
        await pool.close()
