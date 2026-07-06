"""Root-cause #1 regression test: clearing a relay channel on a light
NULLs the channel in DB but preserves the light row."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app.routes.devices import (
    get_config,
    get_database,
    get_relay_manager,
)
from app.routes.devices import (
    router as devices_router,
)


@pytest_asyncio.fixture
async def client():
    app = FastAPI()
    app.include_router(devices_router)

    mock_repo = MagicMock()
    mock_db = MagicMock()
    mock_db.device_repo = mock_repo

    mock_config = MagicMock()
    mock_config._config = {"devices": {}}
    mock_config._config_lock = MagicMock()
    mock_config._config_lock.__enter__ = MagicMock(return_value=None)
    mock_config._config_lock.__exit__ = MagicMock(return_value=None)
    mock_config.write_full_config = MagicMock()
    mock_config.reload = MagicMock()

    app.dependency_overrides[get_database] = lambda: mock_db
    app.dependency_overrides[get_config] = lambda: mock_config
    app.dependency_overrides[get_relay_manager] = lambda: MagicMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ac.mock_repo = mock_repo  # type: ignore[attr-defined]
        ac.mock_config = mock_config  # type: ignore[attr-defined]
        yield ac


class TestClearChannelDevice:
    @pytest.mark.asyncio
    async def test_clear_channel_nulls_light_channel(self, client):
        """DELETE /api/devices/channels/5 on a light must NULL channel, not delete the row."""
        client.mock_repo.get_all_as_hierarchy = AsyncMock(
            return_value={
                "Flower Room": {
                    "main": {
                        "light_f_1": {
                            "device_id": 1,
                            "device_type": "light",
                            "channel": 5,
                            "display_name": "Chilled Front",
                        }
                    }
                }
            }
        )
        client.mock_repo.clear_relay_binding_only = AsyncMock(return_value=True)

        response = await client.delete("/api/devices/channels/5")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["cleared"] is True

        # Root-cause #1 fix: light must survive with NULL channel
        client.mock_repo.clear_relay_binding_only.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_clear_channel_preserves_non_light_yaml_behavior(self, client):
        """Non-lights are still removed from YAML config."""
        client.mock_repo.get_all_as_hierarchy = AsyncMock(
            return_value={
                "Flower Room": {
                    "main": {
                        "heater_1": {
                            "device_id": 2,
                            "device_type": "heater",
                            "channel": 3,
                        }
                    }
                }
            }
        )
        client.mock_repo.clear_relay_binding_only = AsyncMock(return_value=True)

        client.mock_config._config = {
            "devices": {
                "Flower Room": {
                    "main": {
                        "heater_1": {"device_type": "heater", "channel": 3},
                    }
                }
            }
        }

        response = await client.delete("/api/devices/channels/3")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        # Non-light: no DB NULLing, but YAML deletion happened
        client.mock_repo.clear_relay_binding_only.assert_not_awaited()
        client.mock_config.write_full_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_channel_out_of_range(self, client):
        response = await client.delete("/api/devices/channels/20")
        assert response.status_code == 400
