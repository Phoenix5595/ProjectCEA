"""DFRobot SEN0604 4-in-1 soil probe register map and decoding.

Registers (Modbus RTU, function 0x03): ``0x0000`` moisture ×10 %,
``0x0001`` temperature ×10 °C (signed two's complement), ``0x0002`` EC µS/cm,
``0x0003`` pH ×10. ``0x07D0`` holds the device address 1–254 (function 0x06).
"""

from __future__ import annotations

from collections.abc import Sequence

SEN0604_DATA_REGISTER = 0x0000
SEN0604_DATA_COUNT = 4
SEN0604_ADDRESS_REGISTER = 0x07D0
FACTORY_ADDRESS = 1
ASSIGNABLE_ADDRESSES = range(2, 255)


def _signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def decode_sen0604(registers: Sequence[int]) -> dict[str, float]:
    """Decode the four SEN0604 data registers into wire-keyed readings."""
    if len(registers) != SEN0604_DATA_COUNT:
        raise ValueError(f"SEN0604 reply needs {SEN0604_DATA_COUNT} registers, got {len(registers)}")
    moisture, temperature, ec, ph = registers
    return {
        "humidity": moisture / 10,
        "temperature": _signed16(temperature) / 10,
        "ec": float(ec),
        "ph": ph / 10,
    }
