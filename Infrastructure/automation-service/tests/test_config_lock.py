"""Tests for ConfigLoader thread-safety: threading.Lock and concurrent writes."""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
from threading import Thread

import pytest
import yaml


class TestConfigLock:
    """Concurrency tests for ConfigLoader._config_lock."""

    @pytest.fixture
    def temp_config_file(self):
        """Create a minimal valid temp config with two rooms and global channel uniqueness.

        ProjectCEA enforces global relay channel uniqueness (0-15 across ALL rooms).
        Room0 has room0_device on channel 0; Room1 has room1_device on channel 15.
        Each thread repeatedly updates its pre-existing device (changing display_name only,
        not channel) so no channel collisions occur.
        """
        data = {
            "devices": {
                "Room0": {
                    "main": {
                        "room0_device": {
                            "device_type": "heating",
                            "channel": 0,
                        },
                    },
                },
                "Room1": {
                    "main": {
                        "room1_device": {
                            "device_type": "heating",
                            "channel": 15,
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
        yield Path(path)
        Path(path).unlink(missing_ok=True)

    def test_concurrent_write_no_exception(self, temp_config_file):
        """Two threads each doing 15 updates to their own device must not raise."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=str(temp_config_file))

        lock_errors: list[Exception] = []

        def writer(thread_id: int) -> None:
            room = f"Room{thread_id}"
            device = f"room{thread_id}_device"
            try:
                for i in range(15):
                    config._config_lock.acquire()
                    try:
                        config._config["devices"][room]["main"][device]["display_name"] = (
                            f"Updated_{thread_id}_{i}"
                        )
                        config.write_full_config(config._config)
                    finally:
                        config._config_lock.release()
            except Exception as e:
                lock_errors.append(e)

        t1 = Thread(target=writer, args=(0,))
        t2 = Thread(target=writer, args=(1,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert lock_errors == [], f"Lock-protected write raised: {lock_errors}"

    def test_lock_is_threading_lock(self, temp_config_file):
        """Verify the lock is a standard threading.Lock (not reentrant)."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=str(temp_config_file))
        lock = config._config_lock
        assert hasattr(lock, "acquire") and hasattr(lock, "release")
        assert type(lock) is type(threading.Lock())

    def test_all_30_updates_persisted(self, temp_config_file):
        """After 30 concurrent updates, the last value must be on disk for each control field."""
        from app.config import ConfigLoader

        config = ConfigLoader(config_path=str(temp_config_file))

        def writer(thread_id: int) -> None:
            field = f"test_field_{thread_id}"
            for i in range(15):
                config._config_lock.acquire()
                try:
                    config._config["control"][field] = f"Updated_{thread_id}_{i}"
                    config.write_full_config(config._config)
                finally:
                    config._config_lock.release()

        t1 = Thread(target=writer, args=(0,))
        t2 = Thread(target=writer, args=(1,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        with open(temp_config_file) as f:
            disk = yaml.safe_load(f)

        for tid in (0, 1):
            field = f"test_field_{tid}"
            name = disk["control"].get(field, "")
            assert name.startswith(f"Updated_{tid}_"), f"{field} was not updated: {name}"
