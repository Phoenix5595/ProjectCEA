"""Single owner of the RS-485 soil bus.

One :class:`ModbusRTU` and one :class:`asyncio.Lock`: the poller and
discovery interleave transaction by transaction (``asyncio.Lock`` is FIFO),
so two requests never share the wire. Serial I/O runs in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import serial

from shared.infra_logging import get_logger

from .modbus_rtu import ModbusReply, ModbusRTU, ModbusStatus
from .soil_sensor_reader import (
    SEN0604_ADDRESS_REGISTER,
    SEN0604_DATA_COUNT,
    SEN0604_DATA_REGISTER,
)

logger = get_logger(__name__)


class SoilBus:
    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 0.5,
        scan_timeout: float = 0.2,
        inter_frame_delay: float = 0.005,
    ) -> None:
        self.port = port
        self.scan_timeout = scan_timeout
        self._modbus = ModbusRTU(port, baudrate, timeout, inter_frame_delay)
        self._lock = asyncio.Lock()
        # None: no open attempt yet; True: last open/transaction succeeded.
        self._port_healthy: bool | None = None

    async def read_probe(self, address: int, *, scan: bool = False) -> ModbusReply:
        """Read the four SEN0604 data registers at ``address``."""
        timeout = self.scan_timeout if scan else None
        return await self._run(
            lambda: self._modbus.read_holding_registers(
                address, SEN0604_DATA_REGISTER, SEN0604_DATA_COUNT, timeout
            )
        )

    async def write_address(self, current: int, new: int) -> ModbusReply:
        """Write ``new`` into the address register of the probe at ``current``."""
        return await self._run(
            lambda: self._modbus.write_single_register(
                current, SEN0604_ADDRESS_REGISTER, new, self.scan_timeout
            )
        )

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._modbus.disconnect)

    async def _run(self, transaction: Callable[[], ModbusReply]) -> ModbusReply:
        async with self._lock:
            return await asyncio.to_thread(self._guarded, transaction)

    def _guarded(self, transaction: Callable[[], ModbusReply]) -> ModbusReply:
        try:
            if not self._modbus.is_open:
                self._modbus.connect()
            reply = transaction()
        except (serial.SerialException, OSError) as exc:
            self._modbus.disconnect()
            if self._port_healthy is not False:
                logger.error(f"soil RS-485 port {self.port} unavailable: {exc}")
            self._port_healthy = False
            return ModbusReply(ModbusStatus.NO_RESPONSE)
        if self._port_healthy is False:
            logger.info(f"soil RS-485 port {self.port} recovered")
        self._port_healthy = True
        return reply
