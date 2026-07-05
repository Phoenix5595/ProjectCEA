"""Tests for DeviceRepository device registry methods and migration 009.

Covers CRUD on device_registry table and idempotent seed migration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import asyncpg
import pytest
import pytest_asyncio

from app.repositories.devices import DeviceRepository

_DB_URL = "postgresql://cea_user:9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw@localhost:5432/cea_sensors"
_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _alembic_cmd(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(
        [
            str(Path(__file__).resolve().parent.parent.parent),
            str(Path(__file__).resolve().parent.parent),
        ]
    )
    env["POSTGRES_PASSWORD"] = "9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_ALEMBIC_INI), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest_asyncio.fixture
async def pool():
    """Asyncpg pool connected to the test database."""
    p = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=2)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def clean_registry(pool):
    """Truncate device_registry before each test."""
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE device_registry RESTART IDENTITY CASCADE")
    yield


@pytest_asyncio.fixture
async def repo(pool):
    """DeviceRepository instance with a live pool."""
    return DeviceRepository(pool)


class TestDeviceRegistryRepository:
    """CRUD tests for device_registry via DeviceRepository."""

    def setup_class(self):
        _alembic_cmd("upgrade", "008_device_registry").check_returncode()

    @pytest.mark.asyncio
    async def test_get_all_as_hierarchy_empty(self, repo, clean_registry):
        hierarchy = await repo.get_all_as_hierarchy()
        assert hierarchy == {}

    @pytest.mark.asyncio
    async def test_get_all_as_hierarchy_shape(self, repo, clean_registry):
        async with repo.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO device_registry
                    (location, cluster, device_name, display_name, device_type,
                     channel, dimming_enabled, dimming_type, dimming_board_id,
                     dimming_channel, safety_level, pid_enabled, interlock_with,
                     pid_setpoints, per_room_index, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, NOW(), NOW())""",
                "Flower Room",
                "main",
                "light_f_1",
                "Chilled Front",
                "light",
                10,
                True,
                "dfr0971",
                2,
                0,
                0,
                False,
                json.dumps([]),
                json.dumps({}),
                1,
            )

        hierarchy = await repo.get_all_as_hierarchy()
        assert "Flower Room" in hierarchy
        assert "main" in hierarchy["Flower Room"]
        assert "light_f_1" in hierarchy["Flower Room"]["main"]
        assert hierarchy["Flower Room"]["main"]["light_f_1"]["display_name"] == "Chilled Front"

    @pytest.mark.asyncio
    async def test_get_lights_by_room_filters_non_lights(self, repo, clean_registry):
        async with repo.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO device_registry (location, cluster, device_name, device_type, channel, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, NOW(), NOW())""",
                "Flower Room",
                "main",
                "exhaust_fan",
                "fan",
                1,
            )
            await conn.execute(
                """INSERT INTO device_registry
                    (location, cluster, device_name, display_name, device_type,
                     channel, dimming_enabled, dimming_type, dimming_board_id,
                     dimming_channel, safety_level, pid_enabled, interlock_with,
                     pid_setpoints, per_room_index, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, NOW(), NOW())""",
                "Flower Room",
                "main",
                "light_f_1",
                "Chilled Front",
                "light",
                10,
                True,
                "dfr0971",
                2,
                0,
                0,
                False,
                json.dumps([]),
                json.dumps({}),
                1,
            )

        lights = await repo.get_lights_by_room("Flower Room")
        assert len(lights) == 1
        assert lights[0].device_name == "light_f_1"

    @pytest.mark.asyncio
    async def test_get_unbound_lights_by_room(self, repo, clean_registry):
        async with repo.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO device_registry
                    (location, cluster, device_name, display_name, device_type,
                     channel, dimming_enabled, dimming_type, dimming_board_id,
                     dimming_channel, safety_level, pid_enabled, interlock_with,
                     pid_setpoints, per_room_index, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, NOW(), NOW())""",
                "Flower Room",
                "main",
                "light_f_1",
                "Chilled Front",
                "light",
                10,
                True,
                "dfr0971",
                2,
                0,
                0,
                False,
                json.dumps([]),
                json.dumps({}),
                1,
            )
            await conn.execute(
                """INSERT INTO device_registry
                    (location, cluster, device_name, display_name, device_type,
                     channel, dimming_enabled, dimming_type, dimming_board_id,
                     dimming_channel, safety_level, pid_enabled, interlock_with,
                     pid_setpoints, per_room_index, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14::jsonb, $15, NOW(), NOW())""",
                "Flower Room",
                "main",
                "light_f_2",
                "Unbound Light",
                "light",
                None,
                True,
                "dfr0971",
                2,
                1,
                0,
                False,
                json.dumps([]),
                json.dumps({}),
                2,
            )

        unbound = await repo.get_unbound_lights_by_room("Flower Room")
        assert len(unbound) == 1
        assert unbound[0].device_name == "light_f_2"
        assert unbound[0].relay_channel is None

    @pytest.mark.asyncio
    async def test_create_light_generates_device_name(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Veg Room",
            display_name="Eyefinity Top",
            per_room_index=1,
        )
        assert light.device_name == "light_v_1"
        assert light.location == "Veg Room"
        assert light.display_name == "Eyefinity Top"
        assert light.board_id == 0
        assert light.dimming_channel == 0

    @pytest.mark.asyncio
    async def test_create_light_all_rooms(self, repo, clean_registry):
        slots = [
            ("Flower Room", 0, 0),
            ("Veg Room", 0, 1),
            ("Lab", 1, 0),
            ("Outside", 1, 1),
        ]
        for room, board_id, dimming_channel in slots:
            light = await repo.create_light(
                board_id=board_id,
                dimming_channel=dimming_channel,
                room=room,
                display_name="Test",
                per_room_index=1,
            )
            prefix = {"Flower Room": "f", "Veg Room": "v", "Lab": "l", "Outside": "o"}[room]
            assert light.device_name == f"light_{prefix}_1"

    @pytest.mark.asyncio
    async def test_update_light_changes_fields(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Old Name",
            per_room_index=1,
        )
        updated = await repo.update_light(light.device_id, display_name="New Name", safety_level=40)
        assert updated is not None
        assert updated.display_name == "New Name"
        assert updated.safety_level == 40
        assert updated.device_name == "light_f_1"

    @pytest.mark.asyncio
    async def test_update_light_regenerates_device_name_on_room_change(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        updated = await repo.update_light(light.device_id, room="Veg Room")
        assert updated is not None
        assert updated.device_name == "light_v_1"
        assert updated.location == "Veg Room"

    @pytest.mark.asyncio
    async def test_update_light_regenerates_device_name_on_index_change(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        updated = await repo.update_light(light.device_id, per_room_index=5)
        assert updated is not None
        assert updated.device_name == "light_f_5"
        assert updated.per_room_index == 5

    @pytest.mark.asyncio
    async def test_clear_relay_binding_only_preserves_row(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        bound = await repo.bind_relay(light.device_id, 10)
        assert bound is True

        cleared = await repo.clear_relay_binding_only(light.device_id)
        assert cleared is True

        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT channel FROM device_registry WHERE device_id = $1", light.device_id
            )
        assert row["channel"] is None

    @pytest.mark.asyncio
    async def test_bind_relay_sets_channel(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        result = await repo.bind_relay(light.device_id, 7)
        assert result is True

        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT channel FROM device_registry WHERE device_id = $1", light.device_id
            )
        assert row["channel"] == 7

    @pytest.mark.asyncio
    async def test_bind_relay_conflict_check(self, repo, clean_registry):
        light1 = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Light 1",
            per_room_index=1,
        )
        light2 = await repo.create_light(
            board_id=0,
            dimming_channel=1,
            room="Flower Room",
            display_name="Light 2",
            per_room_index=2,
        )
        await repo.bind_relay(light1.device_id, 5)

        result = await repo.bind_relay(light2.device_id, 5)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_light_removes_row(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        deleted = await repo.delete_light(light.device_id)
        assert deleted is True

        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM device_registry WHERE device_id = $1", light.device_id
            )
        assert row is None

    @pytest.mark.asyncio
    async def test_rename_and_regenerate_device_name(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        renamed = await repo.rename_and_regenerate_device_name(light.device_id, "Veg Room", 3)
        assert renamed is not None
        assert renamed.device_name == "light_v_3"
        assert renamed.location == "Veg Room"
        assert renamed.per_room_index == 3

    @pytest.mark.asyncio
    async def test_update_light_rejects_invalid_fields(self, repo, clean_registry):
        light = await repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        with pytest.raises(ValueError, match="Invalid fields"):
            await repo.update_light(light.device_id, bad_field="x")


class TestSeedMigration:
    """End-to-end alembic migration test for 009 seed."""

    def setup_class(self):
        _alembic_cmd("downgrade", "007_pid_per_room").check_returncode()

    def _count_devices(self) -> int:
        from sqlalchemy import create_engine, text

        engine = create_engine(_DB_URL)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT COUNT(*) FROM device_registry")).fetchone()
        engine.dispose()
        return row[0] if row else 0

    def test_upgrade_seeds_devices(self):
        result = _alembic_cmd("upgrade", "009_seed_device_registry")
        assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"

        count = self._count_devices()
        assert count > 0, "No devices seeded by migration 009"

    def test_seed_idempotency(self):
        _alembic_cmd("upgrade", "009_seed_device_registry").check_returncode()
        count_first = self._count_devices()

        _alembic_cmd("downgrade", "008_device_registry").check_returncode()
        count_after_downgrade = self._count_devices()
        assert count_after_downgrade == 0, "Downgrade did not clear device_registry"

        _alembic_cmd("upgrade", "009_seed_device_registry").check_returncode()
        count_second = self._count_devices()

        assert count_first == count_second, (
            f"Idempotency failed: first={count_first}, second={count_second}"
        )

    def test_downgrade_clears_table(self):
        _alembic_cmd("upgrade", "009_seed_device_registry").check_returncode()
        assert self._count_devices() > 0

        _alembic_cmd("downgrade", "008_device_registry").check_returncode()
        assert self._count_devices() == 0
