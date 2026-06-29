"""Tests verifying simulation code is removed from production drivers.

Hardware probe failure must be FATAL — never silently fall back to simulation.
These tests use a FakeSMBus to simulate I2C buses for the success path and
force a probe failure for the error path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Ensure service root and Infrastructure/ (for shared.*) are importable
_SERVICE_ROOT = Path(__file__).parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))
_INFRA_ROOT = _SERVICE_ROOT.parent
if str(_INFRA_ROOT) not in sys.path:
    sys.path.insert(0, str(_INFRA_ROOT))

from app.hardware.mcp23017 import MCP23017Driver
from app.hardware.dfr0971 import DFR0971Driver, DFR0971Manager


# ---------------------------------------------------------------------------
# FakeSMBus: in-memory I2C bus state
# ---------------------------------------------------------------------------


class FakeSMBus:
    """Minimal in-memory fake of smbus2.SMBus for unit tests.

    Maintains 256-byte register banks per address for write/read operations.
    write_byte_data / read_byte_data cover the MCP23017 surface; write_byte
    covers DFR0971's CMD_STORE; write_word_data covers DFR0971's voltage set.
    """

    _instances: list["FakeSMBus"] = []

    def __init__(self, bus_num: int) -> None:
        self.bus_num = bus_num
        # 256-byte register bank per address; addresses added on demand
        self._regs: dict[int, bytearray] = {}
        self.fail_next_op: bool = False
        FakeSMBus._instances.append(self)

    def _bank(self, addr: int) -> bytearray:
        if addr not in self._regs:
            self._regs[addr] = bytearray(256)
        return self._regs[addr]

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        if self.fail_next_op:
            self.fail_next_op = False
            raise OSError(f"FakeSMBus: forced I2C failure at addr=0x{addr:02x}")
        self._bank(addr)[reg] = value & 0xFF

    def read_byte_data(self, addr: int, reg: int) -> int:
        if self.fail_next_op:
            self.fail_next_op = False
            raise OSError(f"FakeSMBus: forced I2C failure at addr=0x{addr:02x}")
        return self._bank(addr)[reg]

    def write_byte(self, addr: int, value: int) -> None:
        if self.fail_next_op:
            self.fail_next_op = False
            raise OSError(f"FakeSMBus: forced I2C failure at addr=0x{addr:02x}")
        # No register for byte-only writes; ignore

    def write_word_data(self, addr: int, reg: int, value: int) -> None:
        if self.fail_next_op:
            self.fail_next_op = False
            raise OSError(f"FakeSMBus: forced I2C failure at addr=0x{addr:02x}")
        bank = self._bank(addr)
        bank[reg] = value & 0xFF
        bank[(reg + 1) & 0xFF] = (value >> 8) & 0xFF

    def close(self) -> None:
        # No-op
        pass

    @classmethod
    def reset(cls) -> None:
        cls._instances.clear()


@pytest.fixture(autouse=True)
def reset_fake_smbus():
    FakeSMBus.reset()
    yield
    FakeSMBus.reset()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(hardware: dict) -> SimpleNamespace:
    """Build a config object compatible with container._init_hardware's usage.

    container._init_hardware calls self.config.get("hardware", {}).
    SimpleNamespace is duck-typed as a config and avoids type-checker conflicts.
    """
    cfg = SimpleNamespace()
    cfg._config = {"hardware": hardware}
    cfg.get = lambda key, default=None: cfg._config.get(key, default)  # type: ignore[method-assign]
    return cfg


# ---------------------------------------------------------------------------
# MCP23017Driver: simulation parameter & attribute must be gone
# ---------------------------------------------------------------------------


class TestMCP23017NoSimulation:
    """simulation parameter and attribute must be removed from MCP23017Driver."""

    def test_no_simulation_parameter_in_constructor(self):
        """MCP23017Driver.__init__ must not accept a 'simulation' keyword argument."""
        import inspect

        sig = inspect.signature(MCP23017Driver.__init__)
        assert "simulation" not in sig.parameters, (
            f"MCP23017Driver.__init__ must not accept 'simulation'; "
            f"got params: {list(sig.parameters)}"
        )

    def test_no_simulation_attribute(self):
        """MCP23017Driver instance must not expose a 'simulation' attribute."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
        assert not hasattr(driver, "simulation"), (
            "MCP23017Driver must not expose 'simulation' attribute"
        )

    def test_constructor_no_kwargs_works(self):
        """MCP23017Driver(i2c_bus=0, i2c_address=0x20) must succeed under FakeSMBus."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
        assert driver.bus is not None
        assert driver.i2c_address == 0x20


# ---------------------------------------------------------------------------
# MCP23017Driver: probe failure is FATAL
# ---------------------------------------------------------------------------


class TestMCP23017ProbeFailureFatal:
    """When probe() fails on real hardware, the driver must not silently run.

    The driver itself may still construct (the I2C bus open can succeed even
    if the device is missing); probe() must return False. It is the *container*
    that raises RuntimeError on probe failure. The driver surface here is:
    - probe() returns False on I2C error
    - is_connected() returns False when probe has failed
    """

    def test_probe_returns_false_on_i2c_error(self):
        """probe() returns False (not raises) when I2C read fails."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
            # Force the next read to fail
            driver.bus.fail_next_op = True
            assert driver.probe() is False

    def test_is_connected_false_after_probe_failure(self):
        """is_connected() returns False when probe failed."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
            driver.bus.fail_next_op = True
            driver.probe()
            assert driver.is_connected() is False

    def test_probe_succeeds_with_fake_smbus(self):
        """probe() returns True when the FakeSMBus responds normally."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
            assert driver.probe() is True
            assert driver.is_connected() is True


# ---------------------------------------------------------------------------
# MCP23017Driver: _channel_states preserved (used in real hardware path)
# ---------------------------------------------------------------------------


class TestMCP23017ChannelStatesPreserved:
    """_channel_states must remain — it tracks state in the real hardware path."""

    def test_channel_states_initialized(self):
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
        assert hasattr(driver, "_channel_states")
        assert len(driver._channel_states) == 16
        assert all(s is False for s in driver._channel_states)

    def test_set_channel_updates_tracked_state(self):
        """set_channel must update _channel_states in the real hardware path."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
        assert driver.set_channel(3, True) is True
        assert driver._channel_states[3] is True
        assert driver.set_channel(3, False) is True
        assert driver._channel_states[3] is False

    def test_get_all_channels_falls_back_to_tracked_state(self):
        """get_all_channels falls back to _channel_states when read fails."""
        with patch("smbus2.SMBus", new=FakeSMBus):
            driver = MCP23017Driver(i2c_bus=0, i2c_address=0x20)
        driver.set_channel(5, True)
        driver.bus.fail_next_op = True
        states = driver.get_all_channels()
        assert states[5] is True
        for ch in range(16):
            if ch != 5:
                assert states[ch] is False, f"Channel {ch} expected False, got {states[ch]}"


# ---------------------------------------------------------------------------
# DFR0971Driver / DFR0971Manager: simulation parameter & attribute must be gone
# ---------------------------------------------------------------------------


class TestDFR0971NoSimulation:
    """simulation parameter and attribute must be removed from DFR0971 classes."""

    def test_driver_no_simulation_parameter(self):
        import inspect

        sig = inspect.signature(DFR0971Driver.__init__)
        assert "simulation" not in sig.parameters, (
            f"DFR0971Driver.__init__ must not accept 'simulation'; "
            f"got params: {list(sig.parameters)}"
        )

    def test_driver_no_simulation_attribute(self):
        with patch("smbus2.SMBus", new=FakeSMBus):
            d = DFR0971Driver(i2c_bus=1, i2c_address=0x58)
        assert not hasattr(d, "simulation")

    def test_manager_no_simulation_parameter(self):
        import inspect

        sig = inspect.signature(DFR0971Manager.__init__)
        assert "simulation" not in sig.parameters, (
            f"DFR0971Manager.__init__ must not accept 'simulation'; "
            f"got params: {list(sig.parameters)}"
        )

    def test_manager_no_simulation_attribute(self):
        with patch("smbus2.SMBus", new=FakeSMBus):
            mgr = DFR0971Manager(i2c_bus=1)
        assert not hasattr(mgr, "simulation")

    def test_driver_constructs_with_fake_smbus(self):
        with patch("smbus2.SMBus", new=FakeSMBus):
            d = DFR0971Driver(i2c_bus=1, i2c_address=0x58)
        assert d.bus is not None
        assert d.i2c_address == 0x58

    def test_manager_add_board_works_without_simulation_kwarg(self):
        with patch("smbus2.SMBus", new=FakeSMBus):
            mgr = DFR0971Manager(i2c_bus=1)
            assert mgr.add_board(board_id=0, i2c_address=0x58) is True
            assert mgr.get_board(0) is not None


# ---------------------------------------------------------------------------
# Container: probe failure raises RuntimeError (FATAL)
# ---------------------------------------------------------------------------


class TestContainerProbeFailureFatal:
    """Container must raise RuntimeError on probe failure — no silent fallback."""

    def _build_container(self, hardware: dict):
        from app.container import ServiceContainer

        container = ServiceContainer()
        # Bypass the type-checker's strict ConfigLoader requirement by setting
        # the attribute via the descriptor path; ServiceContainer is a regular
        # Python class so this works at runtime.
        object.__setattr__(container, "config", _make_config(hardware))
        return container

    def test_init_hardware_raises_on_mcp_probe_failure(self):
        """When MCP probe fails, _init_hardware must raise RuntimeError."""
        import asyncio

        container = self._build_container(
            hardware={
                "i2c_bus": 0,
                "mcp_i2c_bus": 0,
                "dfr0971_i2c_bus": 1,
                "i2c_address": 0x20,
                "simulation": False,
                "require_mcp": True,
                "dfr0971_boards": [],
            }
        )

        with patch("smbus2.SMBus", new=FakeSMBus):
            with patch.object(MCP23017Driver, "probe", return_value=False):
                with pytest.raises(RuntimeError) as exc_info:
                    asyncio.run(container._init_hardware())

        assert "I2C" in str(exc_info.value) or "MCP23017" in str(exc_info.value), (
            f"Error message should mention I2C/MCP23017: {exc_info.value}"
        )

    def test_init_hardware_logs_error_before_raising(self, caplog):
        """An ERROR log entry must be emitted before the fatal error is raised."""
        import asyncio
        import logging

        container = self._build_container(
            hardware={
                "i2c_bus": 0,
                "mcp_i2c_bus": 0,
                "dfr0971_i2c_bus": 1,
                "i2c_address": 0x20,
                "simulation": False,
                "require_mcp": True,
                "dfr0971_boards": [],
            }
        )

        with patch("smbus2.SMBus", new=FakeSMBus):
            with patch.object(MCP23017Driver, "probe", return_value=False):
                with caplog.at_level(logging.ERROR, logger="app.container"):
                    with pytest.raises(RuntimeError):
                        asyncio.run(container._init_hardware())

        assert any(
            "MCP23017" in record.message and "probe" in record.message.lower()
            for record in caplog.records
        ), f"Expected ERROR log about MCP23017 probe; got: {[r.message for r in caplog.records]}"

    def test_init_hardware_succeeds_with_fake_smbus(self):
        """When probe succeeds, _init_hardware completes without raising."""
        import asyncio

        container = self._build_container(
            hardware={
                "i2c_bus": 0,
                "mcp_i2c_bus": 0,
                "dfr0971_i2c_bus": 1,
                "i2c_address": 0x20,
                "simulation": False,
                "require_mcp": True,
                "dfr0971_boards": [],
            }
        )

        with patch("smbus2.SMBus", new=FakeSMBus):
            asyncio.run(container._init_hardware())

        assert container.mcp23017 is not None
        assert container.mcp23017.is_connected() is True
