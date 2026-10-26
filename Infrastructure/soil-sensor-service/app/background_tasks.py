"""Background polling and discovery for RS-485 soil probes (R9).

One :class:`SoilBus` owns the serial port. Startup wires every registry
probe (never factory address 1), then the discovery task runs the one-time
startup scan and periodic commissioning cycles while the polling task reads
each wired address in turn. Identity and placement live in the database
(`sensor_registry`, `soil_probe_placement`); a successful read refreshes
``last_seen`` and applies the reconnect rule. The service never chooses a bed.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from shared.db_errors import is_transient_db_error
from shared.infra_logging import get_logger
from shared.replay_buffer import ReplayBuffer
from shared.service_settings import ServiceSettings

from .config import ConfigLoader
from .database import DatabaseManager
from .discovery import ProbeDiscovery
from .modbus_rtu import ModbusStatus
from .redis_client import RedisClient
from .soil_bus import SoilBus
from .soil_sensor_reader import FACTORY_ADDRESS, decode_sen0604

logger = get_logger(__name__)


@dataclass
class WiredProbe:
    registry_id: int
    sensor_names: dict[str, str]
    bed_name: str | None
    room_name: str | None
    failing: bool = False


def probe_name(address: int) -> str:
    return f"soil_sensor_{address}"


class BackgroundTasks:
    """Owns the soil bus, discovery and polling tasks."""

    def __init__(self, config: ConfigLoader, database: DatabaseManager, redis_client: RedisClient):
        self.config = config
        self.database = database
        self.redis_client = redis_client
        self.running = False
        self.task: asyncio.Task[None] | None = None
        self.settings_task: asyncio.Task[None] | None = None
        self.discovery_task: asyncio.Task[None] | None = None
        self.bus: SoilBus | None = None
        self.discovery: ProbeDiscovery | None = None
        self.probes: dict[int, WiredProbe] = {}
        self._pending_readings: ReplayBuffer[
            tuple[str, int, int | None, dict[str, float], datetime]
        ] = ReplayBuffer(720, name="soil_measurements")

    async def start(self) -> None:
        """Open the bus owner, wire registry probes, then start the loops."""
        rs485 = self.config.get_rs485_config()
        self.bus = SoilBus(
            rs485["port"],
            baudrate=int(rs485["baudrate"]),
            timeout=float(rs485["timeout"]),
            scan_timeout=float(rs485["scan_timeout"]),
            inter_frame_delay=float(rs485["inter_frame_delay"]),
        )
        self.discovery = ProbeDiscovery(self.bus, self.database, self.redis_client, self._wire)

        for entry in await self.database.load_registry_sensors():
            address = int(entry["hardware_address"])
            if address == FACTORY_ADDRESS:
                continue
            try:
                await self._wire(entry)
                state = f"assigned to {entry['bed_name'] or entry['room_name']}" if entry["room_name"] else "unassigned"
                logger.info(f"Loaded sensor: {probe_name(address)} ({state})")
            except Exception as e:
                logger.error(f"Failed to load soil probe at address {address}: {e}")

        self.running = True
        pool = await self.database._get_pool()
        self.settings_task = asyncio.create_task(
            ServiceSettings.run_refresh(
                pool,
                "soil",
                self.config.apply_service_settings,
                fallback=self.config.get_service_settings_fallback(),
            )
        )
        self.discovery_task = asyncio.create_task(self._discovery_loop())
        self.task = asyncio.create_task(self._polling_loop())
        logger.info(f"Soil bus on {rs485['port']}: polling and discovery tasks started")

    async def stop(self) -> None:
        """Cancel the loops and close the bus."""
        self.running = False
        for task in (self.discovery_task, self.task, self.settings_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.bus is not None:
            await self.bus.close()
        logger.info("Background polling and discovery tasks stopped")

    async def _wire(self, entry: dict[str, Any]) -> None:
        """Wire one registry identity (and its metric channels) into polling."""
        address = int(entry["hardware_address"])
        registry_id, _device_id, _sensor_ids, sensor_names = (
            await self.database.register_sensor_device(
                int(entry["registry_id"]), address, probe_name(address)
            )
        )
        self.probes[address] = WiredProbe(
            registry_id=registry_id,
            sensor_names=sensor_names,
            bed_name=entry.get("bed_name"),
            room_name=entry.get("room_name"),
        )

    async def _discovery_loop(self) -> None:
        """Startup scan once, then one commissioning cycle per interval."""
        assert self.discovery is not None
        while self.running:
            try:
                if self.discovery.startup_scan_done:
                    await self.discovery.run_cycle()
                else:
                    await self.discovery.startup_scan()
            except Exception as e:
                logger.error(f"Error in soil discovery loop: {e}", exc_info=True)
            interval = self.config.get_polling_config().get("discovery_interval_seconds", 30)
            await asyncio.sleep(interval)

    async def _polling_loop(self) -> None:
        """Poll all wired probes at the configured interval."""
        while self.running:
            interval = self.config.get_polling_config().get("interval_seconds", 5)
            try:
                await self._poll_all_sensors()
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)
            await asyncio.sleep(interval)

    def _queue_reading(
        self,
        name: str,
        address: int,
        registry_id: int | None,
        readings: dict[str, float],
        timestamp: datetime,
    ) -> None:
        if registry_id is None:
            logger.warning(f"Dropping soil measurement for {name}: no registry identity is known")
            return
        self._pending_readings.offer([(name, address, registry_id, readings, timestamp)])

    async def _replay_pending(self) -> None:
        for pending in self._pending_readings.peek(50):
            name, address, registry_id, readings, timestamp = pending
            if registry_id is None:
                logger.warning(f"Dropping soil measurement for {name}: no registry identity is known")
                self._pending_readings.consume(1)
                continue
            try:
                stored_registry_id, _sensor_ids, sensor_names = (
                    await self.database.store_measurements(
                        registry_id, address, name, readings, timestamp
                    )
                )
                probe = self.probes.get(address)
                if probe is not None and probe.registry_id == stored_registry_id:
                    probe.sensor_names = sensor_names
            except Exception as e:
                if is_transient_db_error(e):
                    logger.error(f"Error replaying soil measurements for {name}: {e}")
                    break
                logger.error(
                    f"Dropping soil measurement for {name} after permanent database error: {e}"
                )
            self._pending_readings.consume(1)

    async def _poll_all_sensors(self) -> None:
        """Replay failed database writes, then read every wired probe."""
        assert self.bus is not None
        await self._replay_pending()

        timestamp = datetime.now()
        for address in sorted(self.probes):
            name = probe_name(address)
            reply = await self.bus.read_probe(address)
            probe = self.probes[address]
            if reply.status != ModbusStatus.OK:
                if not probe.failing:
                    logger.warning(f"Failed to read {name}: {reply.status}")
                probe.failing = True
                continue
            if probe.failing:
                logger.info(f"{name} answering again")
                probe.failing = False

            readings = decode_sen0604(reply.registers)
            db_error: Exception | None = None
            try:
                entry = await self.database.observe_known_probe(address)
                if entry is None:
                    entry = await self.database.adopt_probe(address)
                if int(entry["registry_id"]) != probe.registry_id:
                    await self._wire(entry)
                    probe = self.probes[address]
                else:
                    probe.bed_name = entry["bed_name"]
                    probe.room_name = entry["room_name"]
            except Exception as e:
                db_error = e

            room_name = probe.room_name or "Flower Room"
            try:
                await self.redis_client.write_to_stream(name, readings, probe.bed_name, room_name)
                await self.redis_client.publish_all_readings(
                    name,
                    readings,
                    probe.bed_name,
                    room_name,
                    sensor_names=probe.sensor_names,
                )
            except Exception as e:
                logger.error(f"Error publishing soil readings for {name}: {e}")

            if db_error is None:
                try:
                    if self._pending_readings:
                        self._queue_reading(name, address, probe.registry_id, readings, timestamp)
                    else:
                        stored_registry_id, _sensor_ids, sensor_names = (
                            await self.database.store_measurements(
                                probe.registry_id, address, name, readings, timestamp
                            )
                        )
                        if stored_registry_id == probe.registry_id:
                            probe.sensor_names = sensor_names
                except Exception as e:
                    db_error = e
            if db_error is not None:
                if is_transient_db_error(db_error):
                    logger.warning(
                        f"Database unavailable for soil measurements for {name}; "
                        f"buffering: {db_error}"
                    )
                    self._queue_reading(name, address, probe.registry_id, readings, timestamp)
                else:
                    logger.error(
                        f"Dropping soil measurement for {name} after permanent database "
                        f"error: {db_error}"
                    )

            logger.info(
                f"Read {name}: H={readings['humidity']:.1f}% "
                f"T={readings['temperature']:.1f}°C "
                f"EC={readings['ec']:.0f}µS/cm pH={readings['ph']:.1f}"
            )
