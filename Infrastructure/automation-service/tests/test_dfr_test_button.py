"""Tests for POST /api/lights/{device_id}/test DFR sweep endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app.models.device_registry import LightDevice
from app.routes.lights import (
    get_database,
    get_device_repo,
    get_dfr0971_manager,
    get_relay_manager,
)
from app.routes.lights import (
    router as lights_router,
)


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(lights_router)

    mock_repo = MagicMock()
    mock_dfr = MagicMock()
    mock_db = MagicMock()
    mock_relay = MagicMock()

    app.dependency_overrides[get_device_repo] = lambda: mock_repo
    app.dependency_overrides[get_dfr0971_manager] = lambda: mock_dfr
    app.dependency_overrides[get_database] = lambda: mock_db
    app.dependency_overrides[get_relay_manager] = lambda: mock_relay

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.mock_repo = mock_repo  # type: ignore[attr-defined]
        ac.mock_dfr = mock_dfr  # type: ignore[attr-defined]
        ac.mock_db = mock_db  # type: ignore[attr-defined]
        ac.mock_relay = mock_relay  # type: ignore[attr-defined]
        yield ac


class TestDfrTestButton:
    @pytest.mark.asyncio
    async def test_test_light_sweep_and_restore(self, client):
        """DFR sweep runs 100% -> 10% -> 100% and restores prior intensity."""
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test Light",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
                relay_channel=5,
            )
        )
        client.mock_db._automation_redis = MagicMock()
        client.mock_db._automation_redis.read_failsafe = MagicMock(return_value=None)
        client.mock_db.device_repo = client.mock_repo
        client.mock_repo.set_device_state = AsyncMock(return_value=True)

        # Prior intensity is 42%
        client.mock_dfr.get_intensity = MagicMock(return_value=42.0)
        client.mock_dfr.set_intensity = MagicMock(return_value=True)

        # Prior relay state
        client.mock_relay.get_device_state = MagicMock(return_value=0)
        client.mock_relay.get_device_mode = MagicMock(return_value="auto")
        client.mock_relay.set_device_state = MagicMock()

        with patch("app.routes.lights.asyncio.sleep", new_callable=AsyncMock):
            response = await client.post("/api/lights/1/test")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["prior_intensity"] == 42.0

        # Verify sweep sequence: 100, 10, 100
        calls = client.mock_dfr.set_intensity.call_args_list
        assert len(calls) == 4  # 100, 10, 100, restore
        assert calls[0] == ((0, 0, 100.0), {})
        assert calls[1] == ((0, 0, 10.0), {})
        assert calls[2] == ((0, 0, 100.0), {})
        # Finally restores prior intensity
        assert calls[3] == ((0, 0, 42.0), {})

        # Relay turned ON for test, then restored to OFF
        relay_calls = client.mock_relay.set_device_state.call_args_list
        assert relay_calls[0] == (("Flower Room", "main", "light_f_1", 1), {})
        assert relay_calls[1] == (("Flower Room", "main", "light_f_1", 0), {})

    @pytest.mark.asyncio
    async def test_test_light_failsafe_blocks(self, client):
        """Test is blocked when room is in failsafe mode."""
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test Light",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
            )
        )
        client.mock_db._automation_redis = MagicMock()
        client.mock_db._automation_redis.read_failsafe = MagicMock(
            return_value={"reason": "sensor_timeout"}
        )

        response = await client.post("/api/lights/1/test")
        assert response.status_code == 423

    @pytest.mark.asyncio
    async def test_test_light_not_found(self, client):
        client.mock_repo.get_light_by_id = AsyncMock(return_value=None)

        response = await client.post("/api/lights/999/test")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_test_light_i2c_busy(self, client):
        """Test returns 409 when I2C lock is already held."""
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test Light",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
            )
        )
        client.mock_db._automation_redis = MagicMock()
        client.mock_db._automation_redis.read_failsafe = MagicMock(return_value=None)

        # Acquire the real module-level lock before the request
        from app.hardware.i2c_lock import _i2c_bus_1_lock

        async with _i2c_bus_1_lock:
            response = await client.post("/api/lights/1/test")

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_test_light_unbound_no_relay_restore(self, client):
        """Unbound light skips relay state save/restore."""
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test Light",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
                relay_channel=None,
            )
        )
        client.mock_db._automation_redis = MagicMock()
        client.mock_db._automation_redis.read_failsafe = MagicMock(return_value=None)
        client.mock_dfr.get_intensity = MagicMock(return_value=50.0)
        client.mock_dfr.set_intensity = MagicMock(return_value=True)

        with patch("app.routes.lights.asyncio.sleep", new_callable=AsyncMock):
            response = await client.post("/api/lights/1/test")

        assert response.status_code == 200
        client.mock_relay.set_device_state.assert_not_called()
