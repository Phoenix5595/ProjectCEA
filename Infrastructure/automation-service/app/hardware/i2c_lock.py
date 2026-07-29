"""Process-wide asyncio.Lock for I2C bus 1 (DFR0971 dimming operations).

All DFR0971 writes must acquire this lock to prevent interleaved I2C
commands from different coroutines.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from threading import RLock

_i2c_bus_1_lock = asyncio.Lock()
_i2c_bus_0_lock = RLock()


async def acquire_i2c_bus_1() -> asyncio.Lock:
    """Return the process-wide I2C bus 1 lock."""
    return _i2c_bus_1_lock


def get_i2c_bus_0_lock() -> AbstractContextManager[bool]:
    """Return the process-wide synchronous lock for MCP23017 bus 0 operations."""
    return _i2c_bus_0_lock
