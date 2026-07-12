"""CRUD tests for new light endpoints in app/routes/lights.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app.models.device_registry import LightDevice
from app.routes.lights import get_device_repo, get_database
from app.routes.lights import router as lights_router


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(lights_router)
    mock_repo = MagicMock()
    mock_db = MagicMock()
    app.dependency_overrides[get_device_repo] = lambda: mock_repo
    app.dependency_overrides[get_database] = lambda: mock_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.mock_repo = mock_repo  # type: ignore[attr-defined]
        ac.mock_db = mock_db  # type: ignore[attr-defined]
        yield ac


class TestCreateLight:
    @pytest.mark.asyncio
    async def test_create_light_success(self, client):
        client.mock_repo.get_all_as_hierarchy = AsyncMock(return_value={})
        client.mock_repo.get_lights_by_room = AsyncMock(return_value=[])
        client.mock_repo.create_light = AsyncMock(
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

        response = await client.post(
            "/api/lights",
            json={
                "board_id": 0,
                "dimming_channel": 0,
                "room": "Flower Room",
                "display_name": "Test Light",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["device_name"] == "light_f_1"
        assert data["display_name"] == "Test Light"
        client.mock_repo.create_light.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_light_conflict(self, client):
        client.mock_repo.get_all_as_hierarchy = AsyncMock(
            return_value={
                "Flower Room": {
                    "main": {
                        "light_f_1": {
                            "dimming_board_id": 0,
                            "dimming_channel": 0,
                        }
                    }
                }
            }
        )

        response = await client.post(
            "/api/lights",
            json={
                "board_id": 0,
                "dimming_channel": 0,
                "room": "Veg Room",
                "display_name": "Another Light",
            },
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_create_light_auto_index(self, client):
        client.mock_repo.get_all_as_hierarchy = AsyncMock(return_value={})
        existing = LightDevice(
            device_id=1,
            board_id=0,
            dimming_channel=0,
            display_name="Existing",
            per_room_index=3,
            device_name="light_f_3",
            location="Flower Room",
            cluster="main",
        )
        client.mock_repo.get_lights_by_room = AsyncMock(return_value=[existing])
        client.mock_repo.create_light = AsyncMock(
            return_value=LightDevice(
                device_id=2,
                board_id=1,
                dimming_channel=1,
                display_name="New Light",
                per_room_index=4,
                device_name="light_f_4",
                location="Flower Room",
                cluster="main",
            )
        )

        response = await client.post(
            "/api/lights",
            json={
                "board_id": 1,
                "dimming_channel": 1,
                "room": "Flower Room",
                "display_name": "New Light",
            },
        )
        assert response.status_code == 200
        _, kwargs = client.mock_repo.create_light.call_args
        assert kwargs["per_room_index"] == 4


class TestUpdateLight:
    @pytest.mark.asyncio
    async def test_update_light_display_name(self, client):
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Old Name",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
            )
        )
        client.mock_repo.update_light = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="New Name",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
            )
        )
        client.mock_repo.cascade_device_name_change = AsyncMock(return_value=None)

        response = await client.put(
            "/api/lights/1",
            json={"display_name": "New Name"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "New Name"
        client.mock_repo.cascade_device_name_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_light_cascades_on_room_change(self, client):
        client.mock_repo.get_light_by_id = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test",
                per_room_index=1,
                device_name="light_f_1",
                location="Flower Room",
                cluster="main",
            )
        )
        client.mock_repo.update_light = AsyncMock(
            return_value=LightDevice(
                device_id=1,
                board_id=0,
                dimming_channel=0,
                display_name="Test",
                per_room_index=1,
                device_name="light_v_1",
                location="Veg Room",
                cluster="main",
            )
        )
        client.mock_repo.cascade_device_name_change = AsyncMock(return_value=None)

        response = await client.put(
            "/api/lights/1",
            json={"room": "Veg Room"},
        )
        assert response.status_code == 200
        client.mock_repo.cascade_device_name_change.assert_awaited_once_with(
            old_name="light_f_1",
            new_name="light_v_1",
            location="Flower Room",
            cluster="main",
        )

    @pytest.mark.asyncio
    async def test_update_light_not_found(self, client):
        client.mock_repo.get_light_by_id = AsyncMock(return_value=None)

        response = await client.put(
            "/api/lights/999",
            json={"display_name": "New Name"},
        )
        assert response.status_code == 404


class TestDeleteLight:
    @pytest.mark.asyncio
    async def test_delete_light_unbound(self, client):
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
        client.mock_repo.delete_light = AsyncMock(return_value=True)

        response = await client.delete("/api/lights/1")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "warning" not in data

    @pytest.mark.asyncio
    async def test_delete_light_bound_warns(self, client):
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
        client.mock_repo.delete_light = AsyncMock(return_value=True)

        response = await client.delete("/api/lights/1")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "warning" in data
        assert "relay channel 5" in data["warning"]

    @pytest.mark.asyncio
    async def test_delete_light_not_found(self, client):
        client.mock_repo.get_light_by_id = AsyncMock(return_value=None)

        response = await client.delete("/api/lights/999")
        assert response.status_code == 404
