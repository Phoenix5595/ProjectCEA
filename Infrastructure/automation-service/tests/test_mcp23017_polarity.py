"""Tests for MCP23017Driver polarity inversion (active_low flag).

SainSmart 16-channel relay boards are active-LOW ("Low Level Trigger"):
a LOW bit on MCP23017 energizes the relay. The driver must therefore:
  - Initialize all GPIO bits HIGH (0xFF) so all relays are OFF at boot.
  - Invert logical->physical on write (set_channel): logical ON writes bit LOW.
  - Invert physical->logical on read (get_channel): physical LOW means logical ON.

These tests pin the behavior via a FakeSMBus that mocks smbus2.SMBus.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import patch

import pytest

# Register a fake smbus2 module before importing the driver so the
# driver's `import smbus2` resolves without the real package.


class FakeSMBus:
    """In-memory stand-in for smbus2.SMBus.

    Records every write so tests can assert exact I2C traffic.
    Holds register state in a dict so reads return what was last written.
    """

    def __init__(self, bus: int) -> None:
        self.bus = bus
        self.regs: dict[tuple[int, int], int] = {}
        self.writes: list[tuple[int, int, int]] = []  # (addr, reg, value)
        self._read_seq: dict[tuple[int, int], list[int]] = {}
        self._read_default: dict[tuple[int, int], int] = {}

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        self.writes.append((addr, reg, value & 0xFF))
        self.regs[(addr, reg)] = value & 0xFF

    def read_byte_data(self, addr: int, reg: int) -> int:
        # Test-driven reads: if a queue is set, pop from it; else return reg state.
        key = (addr, reg)
        if key in self._read_seq and self._read_seq[key]:
            return self._read_seq[key].pop(0)
        if key in self.regs:
            return self.regs[key]
        if key in self._read_default:
            return self._read_default[key]
        return 0

    # Helper for tests
    def set_read(self, addr: int, reg: int, *values: int) -> None:
        self._read_seq[(addr, reg)] = list(values)

    def set_read_default(self, addr: int, reg: int, value: int) -> None:
        self._read_default[(addr, reg)] = value

    def close(self) -> None:
        pass


@pytest.fixture
def fake_smbus():
    """Patch smbus2.SMBus globally so the driver's late import sees the fake."""
    fake = FakeSMBus(bus=1)
    smbus2_mod = types.ModuleType("smbus2")
    smbus2_mod.SMBus = lambda bus=1: fake  # type: ignore[attr-defined]
    saved = sys.modules.get("smbus2")
    sys.modules["smbus2"] = smbus2_mod
    try:
        yield fake
    finally:
        if saved is None:
            sys.modules.pop("smbus2", None)
        else:
            sys.modules["smbus2"] = saved


# Driver constants
GPIOA = 0x12
GPIOB = 0x13
I2C_ADDR = 0x20


# --- ctor & _initialize_hardware -------------------------------------------------


def test_init_writes_0xff_to_all_ports_when_active_low_true(fake_smbus):
    """SainSmart: all relays must be OFF at boot -> physical 0xFF (all HIGH)."""
    from app.hardware.mcp23017 import MCP23017Driver

    MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)

    gpioa_writes = [v for (a, r, v) in fake_smbus.writes if r == GPIOA]
    gpiob_writes = [v for (a, r, v) in fake_smbus.writes if r == GPIOB]
    assert 0xFF in gpioa_writes, f"Expected 0xFF on GPIOA, got {gpioa_writes}"
    assert 0xFF in gpiob_writes, f"Expected 0xFF on GPIOB, got {gpiob_writes}"


def test_init_writes_0x00_to_all_ports_when_active_low_false(fake_smbus):
    """Active-HIGH board: all relays OFF at boot -> physical 0x00 (all LOW)."""
    from app.hardware.mcp23017 import MCP23017Driver

    MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=False)

    gpioa_writes = [v for (a, r, v) in fake_smbus.writes if r == GPIOA]
    gpiob_writes = [v for (a, r, v) in fake_smbus.writes if r == GPIOB]
    assert 0x00 in gpioa_writes, f"Expected 0x00 on GPIOA, got {gpioa_writes}"
    assert 0x00 in gpiob_writes, f"Expected 0x00 on GPIOB, got {gpiob_writes}"


def test_default_polarity_is_active_low(fake_smbus):
    """No-arg ctor must default to active_low=True (SainSmart safety)."""
    from app.hardware.mcp23017 import MCP23017Driver

    MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR)

    gpioa_writes = [v for (a, r, v) in fake_smbus.writes if r == GPIOA]
    assert 0xFF in gpioa_writes, "Default polarity must be active-LOW (SainSmart)"


# --- set_channel polarity --------------------------------------------------------


def test_set_channel_on_clears_bit_when_active_low(fake_smbus):
    """Logical ON + active_low=True -> physical LOW (bit cleared)."""
    from app.hardware.mcp23017 import MCP23017Driver

    # Pre-seed GPIOA with all bits set (relays OFF state on active-LOW board)
    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0xFF
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0xFF

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()  # ignore init writes

    assert drv.set_channel(3, True) is True

    # Bit 3 must be CLEARED in the final GPIOA value.
    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    assert final_a & (1 << 3) == 0, (
        f"set_channel(3, True) with active_low=True must clear bit 3; GPIOA=0x{final_a:02X}"
    )
    # Other bits must be unchanged.
    assert final_a & ~(1 << 3) == 0xFF & ~(1 << 3)


def test_set_channel_off_sets_bit_when_active_low(fake_smbus):
    """Logical OFF + active_low=True -> physical HIGH (bit set)."""
    from app.hardware.mcp23017 import MCP23017Driver

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0x00
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0x00

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()

    assert drv.set_channel(3, False) is True

    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    assert final_a & (1 << 3) != 0, (
        f"set_channel(3, False) with active_low=True must set bit 3; GPIOA=0x{final_a:02X}"
    )


def test_set_channel_active_high_default_behavior(fake_smbus):
    """active_low=False preserves the original (active-HIGH) write semantics."""
    from app.hardware.mcp23017 import MCP23017Driver

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0x00
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0x00

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=False)
    fake_smbus.writes.clear()

    # ON -> physical HIGH (bit set)
    assert drv.set_channel(3, True) is True
    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    assert final_a & (1 << 3) != 0

    # OFF -> physical LOW (bit clear)
    assert drv.set_channel(3, False) is True
    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    assert final_a & (1 << 3) == 0


def test_set_channel_inversion_is_symmetric_round_trip(fake_smbus):
    """Setting and reading the same channel returns the logical state."""
    from app.hardware.mcp23017 import MCP23017Driver

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0xFF
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0xFF

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()

    drv.set_channel(5, True)
    assert drv.get_channel(5) is True

    drv.set_channel(5, False)
    assert drv.get_channel(5) is False


# --- get_channel polarity ---------------------------------------------------------


def test_get_channel_returns_inverted_bit_when_active_low(fake_smbus):
    """Physical bit HIGH on active-LOW board means logical OFF."""
    from app.hardware.mcp23017 import MCP23017Driver

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()

    # Channel 4 is in Port A; bit 4 set -> physical HIGH -> logical OFF.
    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 1 << 4
    assert drv.get_channel(4) is False

    # Channel 4 bit cleared -> physical LOW -> logical ON.
    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0x00
    assert drv.get_channel(4) is True


def test_get_channel_no_inversion_when_active_high(fake_smbus):
    """active_low=False: physical bit directly reflects logical state."""
    from app.hardware.mcp23017 import MCP23017Driver

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=False)
    fake_smbus.writes.clear()

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 1 << 6
    assert drv.get_channel(6) is True

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0x00
    assert drv.get_channel(6) is False


def test_get_channel_port_b_uses_correct_bit_offset(fake_smbus):
    """Channels 8-15 are on Port B with bit offset = channel - 8."""
    from app.hardware.mcp23017 import MCP23017Driver

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()

    # Channel 10 -> Port B, bit 2
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 1 << 2  # physical HIGH -> logical OFF
    assert drv.get_channel(10) is False

    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0x00  # physical LOW -> logical ON
    assert drv.get_channel(10) is True


# --- all_off --------------------------------------------------------------------


def test_all_off_writes_0xff_when_active_low(fake_smbus):
    """all_off() must clear every relay (write 0xFF to both ports for active-LOW)."""
    from app.hardware.mcp23017 import MCP23017Driver

    # Pre-seed: pretend some relays are ON (bits cleared on active-LOW board)
    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0x00
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0x00

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=True)
    fake_smbus.writes.clear()

    assert drv.all_off() is True

    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    final_b = fake_smbus.regs[(I2C_ADDR, GPIOB)]
    assert final_a == 0xFF, f"all_off() must drive GPIOA=0xFF, got 0x{final_a:02X}"
    assert final_b == 0xFF, f"all_off() must drive GPIOB=0xFF, got 0x{final_b:02X}"


def test_all_off_writes_0x00_when_active_high(fake_smbus):
    """all_off() on active-HIGH board writes 0x00 to both ports."""
    from app.hardware.mcp23017 import MCP23017Driver

    fake_smbus.regs[(I2C_ADDR, GPIOA)] = 0xFF
    fake_smbus.regs[(I2C_ADDR, GPIOB)] = 0xFF

    drv = MCP23017Driver(i2c_bus=1, i2c_address=I2C_ADDR, active_low=False)
    fake_smbus.writes.clear()

    assert drv.all_off() is True

    final_a = fake_smbus.regs[(I2C_ADDR, GPIOA)]
    final_b = fake_smbus.regs[(I2C_ADDR, GPIOB)]
    assert final_a == 0x00, f"all_off() must drive GPIOA=0x00, got 0x{final_a:02X}"
    assert final_b == 0x00, f"all_off() must drive GPIOB=0x00, got 0x{final_b:02X}"
