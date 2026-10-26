"""RS-485 probe discovery and address commissioning (R9).

The Modbus address is the probe's device ID. A new SEN0604 is connected
alone at factory address 1; each cycle reads address 1 and, on a valid
reply, writes the lowest address in 2–254 never used by any identity
(registry or address history) and not seen answering since start. The probe
is adopted once it answers at the new address. A probe that keeps its old
address until power-cycled leaves the reservation pending
(``power_cycle_required``). Pre-addressed probes are adopted by the one-time
startup scan of 2–254.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from shared.infra_logging import get_logger

from .modbus_rtu import ModbusStatus
from .soil_bus import SoilBus
from .soil_sensor_reader import ASSIGNABLE_ADDRESSES, FACTORY_ADDRESS

logger = get_logger(__name__)

COLLISION_STREAK = 3

WireProbe = Callable[[dict[str, Any]], Awaitable[None]]


class ProbeDiscovery:
    def __init__(
        self,
        bus: SoilBus,
        database: Any,
        redis_client: Any,
        wire_probe: WireProbe,
        *,
        verify_timeout: float = 3.0,
        verify_interval: float = 0.5,
    ) -> None:
        self.bus = bus
        self.database = database
        self.redis_client = redis_client
        self.wire_probe = wire_probe
        self.verify_timeout = verify_timeout
        self.verify_interval = verify_interval
        self.bus_state = "ok"
        self.reserved_address: int | None = None
        self.corrupt_streak = 0
        self.startup_scan_done = False
        self.answered: set[int] = set()

    async def startup_scan(self) -> None:
        """Adopt every unknown probe already answering at an address in 2–254."""
        known = {int(entry["hardware_address"]) for entry in await self.database.load_registry_sensors()}
        for address in ASSIGNABLE_ADDRESSES:
            if address not in known:
                reply = await self.bus.read_probe(address, scan=True)
                if reply.status == ModbusStatus.OK:
                    logger.info(f"found pre-addressed soil probe at address {address}")
                    await self._adopt(address)
            await asyncio.sleep(0)
        self.startup_scan_done = True

    async def run_cycle(self) -> None:
        """One commissioning step, the absence sweep and the bus-state publish."""
        if not self.startup_scan_done:
            return
        await self._commission()
        closed = await self.database.close_absent_placements()
        if closed:
            logger.info(f"closed {len(closed)} soil placement(s) after 10 min absence")
        await self.redis_client.publish_bus_status(
            self.bus_state,
            self.reserved_address if self.bus_state == "power_cycle_required" else None,
        )

    async def _commission(self) -> None:
        if self.reserved_address is not None:
            reserved = self.reserved_address
            reply = await self.bus.read_probe(reserved, scan=True)
            if reply.status == ModbusStatus.OK:
                await self._finish(reserved)
                return
            factory = await self.bus.read_probe(FACTORY_ADDRESS, scan=True)
            if factory.status == ModbusStatus.OK:
                await self._write_and_verify(reserved)
            return

        reply = await self.bus.read_probe(FACTORY_ADDRESS, scan=True)
        if reply.status in (ModbusStatus.NO_RESPONSE, ModbusStatus.FOREIGN):
            self.corrupt_streak = 0
            self.bus_state = "ok"
            return
        if reply.status in (ModbusStatus.CORRUPT, ModbusStatus.EXCEPTION):
            self.corrupt_streak += 1
            if self.corrupt_streak >= COLLISION_STREAK:
                if self.bus_state != "collision":
                    logger.warning("several new soil probes answer at address 1; connect one at a time")
                self.bus_state = "collision"
            return

        self.corrupt_streak = 0
        reserved = await self.database.reserved_addresses()
        free = sorted(set(ASSIGNABLE_ADDRESSES) - reserved - self.answered)
        if not free:
            if self.bus_state != "no_free_address":
                logger.warning("no free soil probe address left (2-254)")
            self.bus_state = "no_free_address"
            return
        await self._write_and_verify(free[0])

    async def _write_and_verify(self, new: int) -> None:
        await self.bus.write_address(FACTORY_ADDRESS, new)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.verify_timeout
        while True:
            reply = await self.bus.read_probe(new, scan=True)
            if reply.status == ModbusStatus.OK:
                await self._finish(new)
                logger.info(f"commissioned soil probe at address {new}")
                return
            if loop.time() + self.verify_interval > deadline:
                break
            await asyncio.sleep(self.verify_interval)
        self.reserved_address = new
        self.bus_state = "power_cycle_required"
        logger.warning(f"soil probe not answering at address {new} yet: power-cycle it")

    async def _finish(self, address: int) -> None:
        await self._adopt(address)
        self.reserved_address = None
        self.bus_state = "ok"

    async def _adopt(self, address: int) -> None:
        entry = await self.database.adopt_probe(address)
        self.answered.add(address)
        await self.wire_probe(entry)
