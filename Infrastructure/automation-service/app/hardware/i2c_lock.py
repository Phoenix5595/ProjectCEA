"""Process-wide locks for I2C bus access.

The DFR0971 driver holds a synchronous bus-1 lock across blocking writes,
including calls running in worker threads. The asyncio bus-1 lock remains
available to the light test sweep; MCP23017 bus 0 uses its synchronous lock.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
import threading

_i2c_bus_1_lock = asyncio.Lock()
_i2c_bus_0_lock = threading.RLock()
_i2c_bus_1_thread_lock = threading.RLock()


async def acquire_i2c_bus_1() -> asyncio.Lock:
    """Return the process-wide I2C bus 1 lock."""
    return _i2c_bus_1_lock


def get_i2c_bus_0_lock() -> AbstractContextManager[bool]:
    """Return the process-wide synchronous lock for MCP23017 bus 0 operations."""
    return _i2c_bus_0_lock


def get_i2c_bus_1_thread_lock() -> AbstractContextManager[bool]:
    """Return the process-wide synchronous lock for DFR0971 bus 1 operations."""
    return _i2c_bus_1_thread_lock
