"""Pytest fixtures for config validation tests."""

from pathlib import Path

import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valid_config() -> dict:
    """Load valid test configuration."""
    with open(FIXTURES_DIR / "valid_config.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def invalid_duplicate_relay_config() -> dict:
    """Load config with duplicate relay channels."""
    with open(FIXTURES_DIR / "invalid_duplicate_relay.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def invalid_channel_range_config() -> dict:
    """Load config with out-of-range relay channel."""
    with open(FIXTURES_DIR / "invalid_channel_range.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def invalid_dimming_ref_config() -> dict:
    """Load config with invalid dimming board reference."""
    with open(FIXTURES_DIR / "invalid_dimming_ref.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def invalid_device_type_config() -> dict:
    """Load config with invalid device type."""
    with open(FIXTURES_DIR / "invalid_device_type.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def invalid_i2c_bus_config() -> dict:
    """Load config with I2C bus out of range (0-7)."""
    with open(FIXTURES_DIR / "invalid_i2c_bus.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def valid_separate_buses_config() -> dict:
    """Load config with separate MCP and DFR0971 buses (mcp_i2c_bus: 0, dfr0971_i2c_bus: 1)."""
    with open(FIXTURES_DIR / "valid_separate_buses.yaml") as f:
        return yaml.safe_load(f)
