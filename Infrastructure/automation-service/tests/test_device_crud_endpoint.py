"""Tests for the unified /api/devices/registry CRUD endpoints.

Covers GET, POST, PUT, DELETE for both light and non-light devices,
including conflict checking (409), room validation (400), and cascade
behaviour on light deletion.
"""

from __future__ import annotations

import asyncpg
from fastapi import HTTPException
import pytest
import pytest_asyncio

from app.models.device_registry import Device, DeviceCreate, LightDevice
from app.repositories.devices import DeviceRepository
from app.repositories.schedules import ScheduleRepository
from app.routes.devices_crud import (
    create_registry_device,
    delete_registry_device,
    list_registry_devices,
    update_registry_device,
)

_DB_URL = "postgresql://cea_user:9GxVyxUDLu1zy8jNCYDjveRbx7mCCVIw@localhost:5432/cea_sensors_test"


class _MockConfig:
    """Minimal stand-in for ConfigLoader that only supports cache invalidation."""

    def invalidate_device_cache(self) -> None:
        pass


class _MockDatabase:
    """Minimal stand-in for DatabaseManager exposing pool and schedule_repo."""

    def __init__(self, pool: asyncpg.Pool, schedule_repo: ScheduleRepository) -> None:
        self._pool = pool
        self.schedule_repo = schedule_repo

    async def _get_pool(self) -> asyncpg.Pool:
        return self._pool


@pytest_asyncio.fixture
async def pool():
    """Asyncpg pool connected to the test database."""
    p = await asyncpg.create_pool(_DB_URL, min_size=1, max_size=2)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def clean_registry(pool):
    """Truncate device_registry and schedules before each test."""
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE device_registry RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE schedules RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE effective_setpoints RESTART IDENTITY CASCADE")
    yield


@pytest_asyncio.fixture
async def device_repo(pool):
    """DeviceRepository instance with a live pool."""
    return DeviceRepository(pool)


@pytest_asyncio.fixture
async def schedule_repo(pool):
    """ScheduleRepository instance with a live pool."""
    return ScheduleRepository(pool)


@pytest_asyncio.fixture
async def mock_db(pool, schedule_repo):
    """Mock DatabaseManager wired to real pool and schedule_repo."""
    return _MockDatabase(pool, schedule_repo)


# ---------------------------------------------------------------------------
# GET /api/devices/registry
# ---------------------------------------------------------------------------


class TestListRegistryDevices:
    @pytest.mark.asyncio
    async def test_empty_registry(self, device_repo, clean_registry):
        result = await list_registry_devices(device_repo)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_mixed_types(self, device_repo, clean_registry):
        light = await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Light 1",
            per_room_index=1,
        )
        device = await device_repo.create_device(
            DeviceCreate(device_type="heater", room="Flower Room", display_name="Heater", channel=1)
        )
        result = await list_registry_devices(device_repo)
        assert len(result) == 2
        names = {d.device_name for d in result}
        assert names == {light.device_name, device.device_name}


# ---------------------------------------------------------------------------
# POST /api/devices/registry
# ---------------------------------------------------------------------------


class TestCreateRegistryDevice:
    @pytest.mark.asyncio
    async def test_create_light_success(self, device_repo, clean_registry):
        body = {
            "device_type": "light",
            "board_id": 0,
            "dimming_channel": 0,
            "room": "Flower Room",
            "display_name": "Test Light",
        }
        result = await create_registry_device(body, device_repo, _MockConfig())
        assert isinstance(result, LightDevice)
        assert result.device_type == "light"
        assert result.location == "Flower Room"
        assert result.display_name == "Test Light"

    @pytest.mark.asyncio
    async def test_create_light_missing_device_type(self, device_repo, clean_registry):
        body = {"board_id": 0, "dimming_channel": 0, "room": "Flower Room", "display_name": "X"}
        with pytest.raises(HTTPException) as exc_info:
            await create_registry_device(body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 400
        assert "device_type is required" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_light_dfr_conflict_409(self, device_repo, clean_registry):
        await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Existing",
            per_room_index=1,
        )
        body = {
            "device_type": "light",
            "board_id": 0,
            "dimming_channel": 0,
            "room": "Veg Room",
            "display_name": "Conflict",
        }
        with pytest.raises(HTTPException) as exc_info:
            await create_registry_device(body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_non_light_success(self, device_repo, clean_registry):
        body = {
            "device_type": "heater",
            "room": "Flower Room",
            "display_name": "Heater 1",
            "channel": 0,
        }
        result = await create_registry_device(body, device_repo, _MockConfig())
        assert isinstance(result, Device)
        assert result.device_type == "heating"
        assert result.channel == 0

    @pytest.mark.asyncio
    async def test_create_non_light_relay_conflict_409(self, device_repo, clean_registry):
        await device_repo.create_device(
            DeviceCreate(device_type="heating", room="Flower Room", display_name="H1", channel=0)
        )
        body = {
            "device_type": "fan",
            "room": "Veg Room",
            "display_name": "Fan 1",
            "channel": 0,
        }
        with pytest.raises(HTTPException) as exc_info:
            await create_registry_device(body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_invalid_room_400(self, device_repo, clean_registry):
        body = {
            "device_type": "heater",
            "room": "Unknown Room",
            "display_name": "X",
            "channel": 0,
        }
        with pytest.raises(HTTPException) as exc_info:
            await create_registry_device(body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_light_auto_per_room_index(self, device_repo, clean_registry):
        await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="L1",
            per_room_index=1,
        )
        body = {
            "device_type": "light",
            "board_id": 0,
            "dimming_channel": 1,
            "room": "Flower Room",
            "display_name": "L2",
        }
        result = await create_registry_device(body, device_repo, _MockConfig())
        assert isinstance(result, LightDevice)
        assert result.per_room_index == 2


# ---------------------------------------------------------------------------
# PUT /api/devices/registry/{device_id}
# ---------------------------------------------------------------------------


class TestUpdateRegistryDevice:
    @pytest.mark.asyncio
    async def test_update_light_display_name(self, device_repo, clean_registry):
        light = await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Old",
            per_room_index=1,
        )
        body = {"display_name": "New Name"}
        result = await update_registry_device(light.device_id, body, device_repo, _MockConfig())
        assert isinstance(result, LightDevice)
        assert result.display_name == "New Name"

    @pytest.mark.asyncio
    async def test_update_light_room_cascade(self, device_repo, clean_registry):
        light = await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        body = {"room": "Veg Room"}
        result = await update_registry_device(light.device_id, body, device_repo, _MockConfig())
        assert result.device_name == "light_v_1"
        assert result.location == "Veg Room"

    @pytest.mark.asyncio
    async def test_update_non_light_channel(self, device_repo, clean_registry):
        device = await device_repo.create_device(
            DeviceCreate(device_type="heating", room="Flower Room", display_name="H", channel=0)
        )
        body = {"channel": 5}
        result = await update_registry_device(device.device_id, body, device_repo, _MockConfig())
        assert isinstance(result, Device)
        assert result.channel == 5

    @pytest.mark.asyncio
    async def test_update_non_light_relay_conflict_409(self, device_repo, clean_registry):
        d1 = await device_repo.create_device(
            DeviceCreate(device_type="heating", room="Flower Room", display_name="H1", channel=0)
        )
        await device_repo.create_device(
            DeviceCreate(device_type="heating", room="Flower Room", display_name="H2", channel=1)
        )
        body = {"channel": 1}
        with pytest.raises(HTTPException) as exc_info:
            await update_registry_device(d1.device_id, body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_update_not_found(self, device_repo, clean_registry):
        body = {"display_name": "X"}
        with pytest.raises(HTTPException) as exc_info:
            await update_registry_device(99999, body, device_repo, _MockConfig())
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/devices/registry/{device_id}
# ---------------------------------------------------------------------------


class TestDeleteRegistryDevice:
    @pytest.mark.asyncio
    async def test_delete_non_light(self, device_repo, clean_registry, mock_db):
        device = await device_repo.create_device(
            DeviceCreate(device_type="heating", room="Flower Room", display_name="H", channel=0)
        )
        result = await delete_registry_device(device.device_id, device_repo, mock_db, _MockConfig())
        assert result["success"] is True
        assert result["device_id"] == device.device_id

    @pytest.mark.asyncio
    async def test_delete_light_with_cascade(
        self, device_repo, schedule_repo, clean_registry, mock_db
    ):
        light = await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Test",
            per_room_index=1,
        )
        # Seed a schedule referencing the light
        await schedule_repo.create_schedule(
            name="SUN",
            location="Flower Room",
            cluster="main",
            device_name=light.device_name,
            start_time="08:00",
            end_time="20:00",
            enabled=True,
            mode="SUN",
        )
        # Seed an effective_setpoint referencing the light
        async with device_repo.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO effective_setpoints
                    (location, cluster, device_name, timestamp, effective_light_intensity)
                   VALUES ($1, $2, $3, NOW(), $4)""",
                "Flower Room",
                "main",
                light.device_name,
                80.0,
            )

        result = await delete_registry_device(light.device_id, device_repo, mock_db, _MockConfig())
        assert result["success"] is True
        assert result["deleted_schedules"] == 1

        # Verify schedule is gone
        async with device_repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM schedules WHERE device_name = $1", light.device_name
            )
        assert row is None

        # Verify effective_setpoints are gone
        async with device_repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM effective_setpoints WHERE device_name = $1", light.device_name
            )
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, device_repo, clean_registry, mock_db):
        with pytest.raises(HTTPException) as exc_info:
            await delete_registry_device(99999, device_repo, mock_db, _MockConfig())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_light_warning_when_relay_bound(
        self, device_repo, clean_registry, mock_db
    ):
        light = await device_repo.create_light(
            board_id=0,
            dimming_channel=0,
            room="Flower Room",
            display_name="Bound Light",
            per_room_index=1,
        )
        await device_repo.bind_relay(light.device_id, 5)
        result = await delete_registry_device(light.device_id, device_repo, mock_db, _MockConfig())
        assert result["success"] is True
        assert "warning" in result
        assert "relay channel 5" in result["warning"]
