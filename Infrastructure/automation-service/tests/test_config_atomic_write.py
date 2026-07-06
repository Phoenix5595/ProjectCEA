"""Tests for ConfigLoader atomic writes and in-memory validation."""

from __future__ import annotations

import tempfile

import pytest
import yaml


class TestAtomicWrite:
    """Atomic write and in-memory validation tests for ConfigLoader."""

    @pytest.fixture
    def temp_config_file(self):
        """Create a minimal valid temp config file."""
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
        import os

        os.unlink(path)

    def test_validate_in_memory_accepts_valid_config(self, temp_config_file):
        """validate_in_memory returns the candidate dict on success."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)
        candidate = {
            "devices": {
                "Test Room": {
                    "main": {
                        "heater1": {"device_type": "heating", "channel": 1},
                    },
                },
            },
            "hardware": {"mcp_i2c_bus": 0, "dfr0971_i2c_bus": 1},
            "control": {"allow_legacy_flower_main": True},
        }
        result = config.validate_in_memory(candidate)
        assert result is candidate

    def test_validate_in_memory_rejects_bad_i2c_bus(self, temp_config_file):
        """validate_in_memory raises ValueError for out-of-range I2C bus."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)
        bad_candidate = {
            "devices": {},
            "hardware": {"mcp_i2c_bus": 99},
            "control": {"allow_legacy_flower_main": True},
        }
        with pytest.raises(ValueError, match="hardware.mcp_i2c_bus must be between 0 and 7"):
            config.validate_in_memory(bad_candidate)

    def test_validate_in_memory_rejects_bad_update_interval(self, temp_config_file):
        """validate_in_memory raises ValueError for update_interval > 5."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)
        bad_candidate = {
            "devices": {},
            "hardware": {},
            "control": {"update_interval": 99},
        }
        with pytest.raises(ValueError, match="control.update_interval must be between 1 and 5"):
            config.validate_in_memory(bad_candidate)

    def test_write_full_config_updates_disk(self, temp_config_file):
        """write_full_config successfully writes and the file on disk matches."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)
        new_config = {
            "devices": {
                "Test Room": {
                    "main": {
                        "heater1": {"device_type": "heating", "channel": 5},
                        "cooler1": {"device_type": "cooling", "channel": 6},
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

        # Devices are excluded from YAML (M3: devices live in DB)
        assert "devices" not in disk
        assert disk["hardware"]["mcp_i2c_bus"] == 0
        assert disk["control"]["allow_legacy_flower_main"] is True

    def test_write_full_config_validation_failure_leaves_disk_unchanged(self, temp_config_file):
        """When write_full_config validation fails, the disk file must be unchanged."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)

        with open(temp_config_file) as f:
            original_content = f.read()

        bad_config = {
            "devices": {},
            "hardware": {"mcp_i2c_bus": 999},
            "control": {"allow_legacy_flower_main": True},
        }

        config._config_lock.acquire()
        try:
            with pytest.raises(ValueError, match="hardware.mcp_i2c_bus"):
                config.write_full_config(bad_config)
        finally:
            config._config_lock.release()

        with open(temp_config_file) as f:
            disk_content = f.read()

        assert disk_content == original_content, "Disk was modified after failed validation"

    def test_write_full_config_uses_atomic_replace(self, temp_config_file):
        """The on-disk file is always valid YAML (atomic replace, no partial writes)."""
        import os

        from app.config import ConfigLoader

        config = ConfigLoader(config_path=temp_config_file)

        for i in range(20):
            new_config = {
                "devices": {
                    "Test Room": {
                        "main": {
                            f"device_{i}": {"device_type": "heating", "channel": (i % 15) + 1},
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
                loaded = yaml.safe_load(f)
            # Devices are excluded from YAML (M3: devices live in DB)
            assert "devices" not in loaded
            assert loaded["hardware"]["mcp_i2c_bus"] == 0
            assert loaded["control"]["allow_legacy_flower_main"] is True

        stat_after = os.stat(temp_config_file)
        assert stat_after.st_size > 0
