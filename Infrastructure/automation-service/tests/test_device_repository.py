"""Tests for DeviceRepository non-light CRUD methods.

Covers create_device, update_device, delete_device, typed row dispatch,
and global relay channel conflict checking.
"""

from __future__ import annotations

import json

import asyncpg
import pytest
import pytest_asyncio

from app.models.device_registry import DeviceCreate, DeviceUpdate
from app.repositories.devices import DeviceRepository, _row_to_typed_device

_DB_URL = "postgresql://cea_user:9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw@localhost:5432/cea_sensors_test"


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


class TestCreateDevice:
    """CRUD tests for creating non-light devices."""

    @pytest.mark.asyncio
    async def test_create_device_generates_name(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Heater 1",
                channel=0,
            )
        )
        assert device.device_name == "heating_f_1"
        assert device.device_type == "heating"
        assert device.location == "Flower Room"
        assert device.channel == 0

    @pytest.mark.asyncio
    async def test_create_device_all_rooms(self, repo, clean_registry):
        rooms = [
            ("Flower Room", "f"),
            ("Veg Room", "v"),
            ("Lab", "l"),
            ("Outside", "o"),
        ]
        for room, prefix in rooms:
            device = await repo.create_device(
                DeviceCreate(
                    device_type="fan",
                    room=room,
                    display_name="Fan",
                    channel=1,
                )
            )
            assert device.device_name == f"cooling_{prefix}_1"

    @pytest.mark.asyncio
    async def test_create_device_increments_index(self, repo, clean_registry):
        d1 = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Veg Room",
                display_name="Heater 1",
                channel=0,
            )
        )
        assert d1.device_name == "heating_v_1"

        d2 = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Veg Room",
                display_name="Heater 2",
                channel=1,
            )
        )
        assert d2.device_name == "heating_v_2"

    @pytest.mark.asyncio
    async def test_create_device_canonicalizes_type(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="extraction fan",
                room="Flower Room",
                display_name="Exhaust",
                channel=2,
            )
        )
        assert device.device_type == "exhaust"
        assert device.device_name == "exhaust_f_1"

    @pytest.mark.asyncio
    async def test_create_device_preserves_optional_fields(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="humidifier",
                room="Flower Room",
                display_name="Humidifier",
                channel=3,
                pid_enabled=True,
                interlock_with=["exhaust"],
                pid_setpoints={"humidity": 1},
            )
        )
        assert device.pid_enabled is True
        assert device.interlock_with == ["exhaust"]
        assert device.pid_setpoints == {"humidity": 1}


class TestUpdateDevice:
    """CRUD tests for updating non-light devices."""

    @pytest.mark.asyncio
    async def test_update_device_display_name(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Old Name",
                channel=0,
            )
        )
        updated = await repo.update_device(device.device_id, DeviceUpdate(display_name="New Name"))
        assert updated is not None
        assert updated.display_name == "New Name"
        assert updated.device_name == "heating_f_1"

    @pytest.mark.asyncio
    async def test_update_device_channel(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Heater",
                channel=0,
            )
        )
        updated = await repo.update_device(device.device_id, DeviceUpdate(channel=5))
        assert updated is not None
        assert updated.channel == 5

    @pytest.mark.asyncio
    async def test_update_device_rejects_light(self, repo, clean_registry):
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
                "Light",
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
            row = await conn.fetchrow(
                "SELECT device_id FROM device_registry WHERE device_name = 'light_f_1'"
            )
            light_id = row["device_id"]

        with pytest.raises(ValueError, match="update_light"):
            await repo.update_device(light_id, DeviceUpdate(display_name="X"))

    @pytest.mark.asyncio
    async def test_update_device_global_channel_conflict(self, repo, clean_registry):
        await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Heater 1",
                channel=0,
            )
        )
        d2 = await repo.create_device(
            DeviceCreate(
                device_type="fan",
                room="Veg Room",
                display_name="Fan 1",
                channel=1,
            )
        )
        with pytest.raises(ValueError, match="already in use"):
            await repo.update_device(d2.device_id, DeviceUpdate(channel=0))

    @pytest.mark.asyncio
    async def test_update_device_pid_fields(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="co2 tank",
                room="Flower Room",
                display_name="CO2",
                channel=2,
                pid_enabled=False,
            )
        )
        updated = await repo.update_device(
            device.device_id,
            DeviceUpdate(pid_enabled=True, pid_setpoints={"co2": 1}),
        )
        assert updated is not None
        assert updated.pid_enabled is True
        assert updated.pid_setpoints == {"co2": 1}

    @pytest.mark.asyncio
    async def test_update_device_no_fields_returns_current(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Heater",
                channel=0,
            )
        )
        updated = await repo.update_device(device.device_id, DeviceUpdate())
        assert updated is not None
        assert updated.device_name == "heating_f_1"

    @pytest.mark.asyncio
    async def test_update_device_not_found(self, repo, clean_registry):
        updated = await repo.update_device(99999, DeviceUpdate(display_name="X"))
        assert updated is None


class TestDeleteDevice:
    """CRUD tests for deleting non-light devices."""

    @pytest.mark.asyncio
    async def test_delete_device_removes_row(self, repo, clean_registry):
        device = await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="Heater",
                channel=0,
            )
        )
        deleted = await repo.delete_device(device.device_id)
        assert deleted is True

        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM device_registry WHERE device_id = $1", device.device_id
            )
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_device_does_not_delete_light(self, repo, clean_registry):
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
                "Light",
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
            row = await conn.fetchrow(
                "SELECT device_id FROM device_registry WHERE device_name = 'light_f_1'"
            )
            light_id = row["device_id"]

        deleted = await repo.delete_device(light_id)
        assert deleted is False

        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM device_registry WHERE device_id = $1", light_id
            )
        assert row is not None


class TestTypedRowDispatch:
    """Tests for _row_to_typed_device dispatcher."""

    def test_dispatch_light(self):
        from app.models.device_registry import LightDevice

        row = {
            "device_type": "light",
            "dimming_board_id": 0,
            "dimming_channel": 0,
            "dimming_enabled": True,
            "dimming_type": "dfr0971",
            "safety_level": 0,
            "per_room_index": 1,
            "channel": 10,
            "display_name": "Test Light",
            "device_name": "light_f_1",
            "location": "Flower Room",
            "cluster": "main",
        }
        device = _row_to_typed_device(row)
        assert isinstance(device, LightDevice)
        assert device.device_type == "light"
        assert device.board_id == 0

    def test_dispatch_non_light(self):
        row = {
            "device_type": "heating",
            "channel": 0,
            "pid_enabled": True,
            "interlock_with": ["exhaust"],
            "pid_setpoints": {"heating_setpoint": 1},
            "display_name": "Heater",
            "device_name": "heating_f_1",
            "location": "Flower Room",
            "cluster": "main",
        }
        device = _row_to_typed_device(row)
        assert device.device_type == "heating"
        assert device.channel == 0


class TestGetDeviceCountByTypeLocation:
    """Tests for get_device_count_by_type_location helper."""

    @pytest.mark.asyncio
    async def test_count_empty(self, repo, clean_registry):
        count = await repo.get_device_count_by_type_location("heating", "Flower Room")
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_with_devices(self, repo, clean_registry):
        await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="H1",
                channel=0,
            )
        )
        await repo.create_device(
            DeviceCreate(
                device_type="heater",
                room="Flower Room",
                display_name="H2",
                channel=1,
            )
        )
        await repo.create_device(
            DeviceCreate(
                device_type="fan",
                room="Flower Room",
                display_name="F1",
                channel=2,
            )
        )
        count = await repo.get_device_count_by_type_location("heating", "Flower Room")
        assert count == 2
