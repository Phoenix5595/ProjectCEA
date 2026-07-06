"""Tests for ConfigLoader DB-backed device operations."""

from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml


@pytest.fixture
def temp_config_file():
    """Create a minimal valid temp config file with devices in YAML."""
    data = {
        "devices": {
            "Test Room": {
                "main": {
                    "heater1": {
                        "device_type": "heating",
                        "channel": 0,
                    },
                },
            },
        },
        "hardware": {
            "mcp_i2c_bus": 0,
            "dfr0971_i2c_bus": 1,
        },
        "control": {
            "allow_legacy_flower_main": True,
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_get_devices_returns_db_shape(temp_config_file):
    """get_devices returns the hierarchy from DeviceRepository when set."""
    from app.config import ConfigLoader

    config = ConfigLoader(config_path=temp_config_file)

    mock_repo = MagicMock(name="DeviceRepository")
    mock_repo.get_all_as_hierarchy = AsyncMock(
        return_value={
            "Flower Room": {
                "main": {
                    "light_1": {
                        "device_type": "light",
                        "channel": 3,
                        "dimming_enabled": True,
                    }
                }
            }
        }
    )
    config.set_device_repo(mock_repo)

    devices = await config.get_devices()

    assert "Flower Room" in devices
    assert "main" in devices["Flower Room"]
    assert "light_1" in devices["Flower Room"]["main"]
    assert devices["Flower Room"]["main"]["light_1"]["device_type"] == "light"


@pytest.mark.asyncio
async def test_get_devices_falls_back_to_yaml_when_no_repo(temp_config_file):
    """get_devices returns YAML devices when no DeviceRepository is set."""
    from app.config import ConfigLoader

    config = ConfigLoader(config_path=temp_config_file)

    devices = await config.get_devices()

    assert "Test Room" in devices
    assert "main" in devices["Test Room"]
    assert "heater1" in devices["Test Room"]["main"]
    assert devices["Test Room"]["main"]["heater1"]["device_type"] == "heating"


@pytest.mark.asyncio
async def test_get_devices_empty_db_with_yaml_logs_error(temp_config_file, caplog):
    """When DB is empty but YAML has devices, an error about alembic is logged."""
    import logging

    from app.config import ConfigLoader

    config = ConfigLoader(config_path=temp_config_file)

    mock_repo = MagicMock(name="DeviceRepository")
    mock_repo.get_all_as_hierarchy = AsyncMock(return_value={})
    config.set_device_repo(mock_repo)

    with caplog.at_level(logging.ERROR, logger="app.config"):
        await config.get_devices()

    error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("alembic" in m.lower() for m in error_msgs), (
        f"Expected alembic error log, got: {error_msgs}"
    )


def test_write_full_config_excludes_devices(temp_config_file):
    """write_full_config pops devices before writing to disk."""
    from app.config import ConfigLoader

    config = ConfigLoader(config_path=temp_config_file)

    new_config = {
        "devices": {
            "Test Room": {
                "main": {
                    "heater1": {"device_type": "heating", "channel": 5},
                },
            },
        },
        "hardware": {"mcp_i2c_bus": 0, "dfr0971_i2c_bus": 1},
        "control": {"allow_legacy_flower_main": True},
    }

    config._config_lock.acquire()
    try:
        config.write_full_config(new_config)
    finally:
        config._config_lock.release()

    with open(temp_config_file) as f:
        disk = yaml.safe_load(f)

    assert "devices" not in disk
    assert disk["hardware"]["mcp_i2c_bus"] == 0
    assert disk["control"]["allow_legacy_flower_main"] is True


@pytest.mark.asyncio
async def test_update_device_config_updates_db(temp_config_file):
    """update_device_config calls DeviceRepository.update_light when repo is set."""
    from app.config import ConfigLoader

    config = ConfigLoader(config_path=temp_config_file)

    mock_repo = MagicMock(name="DeviceRepository")
    mock_repo.get_device_id = AsyncMock(return_value=42)
    mock_repo.update_light = AsyncMock(return_value={"id": 42, "display_name": "New Name"})
    config.set_device_repo(mock_repo)

    result = await config.update_device_config(
        "Test Room", "main", "heater1", display_name="New Name"
    )

    assert result is True
    mock_repo.get_device_id.assert_awaited_once_with("Test Room", "main", "heater1")
    mock_repo.update_light.assert_awaited_once_with(42, display_name="New Name")
