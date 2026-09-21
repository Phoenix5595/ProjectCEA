"""Background polling tasks for RS-485 soil sensors, registry-backed.

Discovery and polling authority is the `sensor_registry` table (bus
'rs485'), never YAML and never round-robin bed alternation. Startup loads
known addresses, discovery upserts unknown Modbus IDs as unassigned, and
polling refreshes assignments on the existing discovery cadence. All
probes keep storing measurements and writing the raw stream before they
are commissioned; assigned bed metadata is included when present and the
service never chooses a bed.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any, cast

from shared.infra_logging import get_logger

from .config import ConfigLoader
from .database import DatabaseManager
from .modbus_rtu import ModbusRTU
from .redis_client import RedisClient
from .soil_sensor_reader import SoilSensorReader

logger = get_logger(__name__)


class BackgroundTasks:
    """Manages background polling tasks for soil sensors."""

    def __init__(self, config: ConfigLoader, database: DatabaseManager, redis_client: RedisClient):
        """Initialize background tasks.

        Args:
            config: Configuration loader
            database: Database manager
            redis_client: Redis client
        """
        self.config = config
        self.database = database
        self.redis_client = redis_client
        self.running = False
        self.task: asyncio.Task[None] | None = None
        self.discovery_task: asyncio.Task[None] | None = None
        self.sensor_readers: dict[str, SoilSensorReader] = {}
        self.discovered_modbus_ids: set[int] = set()  # Track discovered Modbus IDs
        self.registry_ids: dict[int, int] = {}  # modbus_id -> registry_id
        self.sensor_configs: dict[str, dict[str, Any]] = {}
        self.sensor_ids: dict[str, dict[str, int]] = {}
        self.rs485_port: str | None = None
        self.rs485_baudrate: int = 9600

    async def start(self) -> None:
        """Start background polling: load registry sensors, then loops."""
        rs485_config = self.config.get_rs485_config()
        self.rs485_port = rs485_config["port"]
        self.rs485_baudrate = rs485_config.get("baudrate", 9600)

        # Registry-backed discovery: known RS-485 addresses come from
        # sensor_registry, not YAML.
        await self._load_registry_sensors()

        try:
            for reader in self.sensor_readers.values():
                reader.connect()
            if self.sensor_readers:
                logger.info("Connected to all known soil sensors")
        except Exception as e:
            logger.error(f"Failed to connect to sensors: {e}")
            # Continue anyway, will retry in polling loop

        self.running = True
        self.discovery_task = asyncio.create_task(self._discovery_loop())
        self.task = asyncio.create_task(self._polling_loop())
        logger.info("Background polling and discovery tasks started")

    async def stop(self) -> None:
        """Stop background polling tasks and disconnect readers."""
        self.running = False
        if self.discovery_task:
            self.discovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.discovery_task
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

        for reader in self.sensor_readers.values():
            try:
                reader.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting sensor: {e}")

        logger.info("Background polling and discovery tasks stopped")

    def _probe_name(self, modbus_id: int) -> str:
        return f"soil_sensor_{modbus_id}"

    async def _wire_probe(
        self, modbus_id: int, registry_id: int | None, bed_name: str | None
    ) -> None:
        """Wire one probe into the polling loop with its registry metadata."""
        probe_name = self._probe_name(modbus_id)
        reader = SoilSensorReader(cast(str, self.rs485_port), modbus_id, self.rs485_baudrate)
        reader.connect()

        serial_number = f"MODBUS-{modbus_id}"
        _device_id, sensor_ids = await self.database.register_sensor_device(
            serial_number, probe_name
        )

        self.sensor_readers[probe_name] = reader
        self.sensor_ids[probe_name] = sensor_ids
        if registry_id is not None:
            self.registry_ids[modbus_id] = registry_id
        self.sensor_configs[probe_name] = {"modbus_id": modbus_id, "bed_name": bed_name}

    async def _load_registry_sensors(self) -> None:
        """Wire every probe known to sensor_registry into polling.

        Assigned probes carry their bed; unassigned probes are polled too so
        measurements keep flowing before commissioning.
        """
        self.sensor_configs = {}
        for entry in await self.database.load_registry_sensors():
            modbus_id = entry["hardware_address"]
            try:
                await self._wire_probe(modbus_id, entry["registry_id"], entry["bed_name"])
                self.discovered_modbus_ids.add(modbus_id)
                state = "assigned to " + entry["bed_name"] if entry["bed_name"] else "unassigned"
                logger.info(f"Loaded sensor: soil_sensor_{modbus_id} ({state})")
            except Exception as e:
                # A failed wiring (e.g. serial adapter absent) stays
                # undiscovered so the next scan retries it instead of the
                # probe being lost until a service restart.
                logger.error(f"Failed to load sensor Modbus ID {modbus_id}: {e}")

    async def _discovery_loop(self) -> None:
        """Periodically scan the bus, register new probes, refresh assignments."""
        polling_config = self.config.get_polling_config()
        discovery_interval = polling_config.get(
            "discovery_interval_seconds", 30
        )  # Default 30 seconds
        scan_range_start = 1
        scan_range_end = 254

        logger.info(f"Starting sensor discovery loop (scanning every {discovery_interval} seconds)")

        while self.running:
            try:
                await self._scan_bus_for_sensors(scan_range_start, scan_range_end)
            except Exception as e:
                logger.error(f"Error in discovery loop: {e}", exc_info=True)

            await asyncio.sleep(discovery_interval)

    async def _scan_bus_for_sensors(self, start_id: int, end_id: int) -> None:
        """Scan the bus for new sensors and upsert unknown addresses as
        unassigned registry rows. Polling metadata refreshes here on the
        existing discovery cadence."""
        # Registry metadata refresh happens before bus work so freshness and
        # bed assignment still follow the registry when the serial adapter is
        # absent or the scan fails.
        await self._refresh_assignments()
        try:
            temp_modbus = ModbusRTU(cast(str, self.rs485_port), self.rs485_baudrate, timeout=0.5)
            temp_modbus.connect()

            for modbus_id in range(start_id, end_id + 1):
                try:
                    registers = await asyncio.to_thread(
                        temp_modbus.read_holding_registers, modbus_id, 0x0000, 1
                    )
                    if registers is not None and modbus_id not in self.discovered_modbus_ids:
                        logger.info(f"Discovered new sensor at Modbus ID {modbus_id}")
                        # Discovery never assigns a bed: the probe joins
                        # polling unassigned and stays there until it is
                        # commissioned through Sensor Settings.
                        if await self._auto_register_sensor(modbus_id):
                            self.discovered_modbus_ids.add(modbus_id)
                except Exception as e:
                    # Probe of a slot with no device is the common case here -
                    # debug-level so a tail -f can confirm the scan is running
                    # without flooding journal at INFO.
                    logger.debug(f"Modbus probe id={modbus_id} no response ({type(e).__name__})")

            temp_modbus.disconnect()
        except Exception as e:
            logger.error(f"Error scanning bus: {e}")
            # Don't raise, keep trying

    async def _refresh_assignments(self) -> None:
        """Refresh bed metadata for wired probes from sensor_registry."""
        registry_rows = await self.database.load_registry_sensors()
        wired_ids = [
            entry["hardware_address"]
            for entry in registry_rows
            if self._probe_name(entry["hardware_address"]) in self.sensor_configs
        ]
        await self.database.touch_last_seen(wired_ids)
        for entry in registry_rows:
            modbus_id = entry["hardware_address"]
            probe_name = self._probe_name(modbus_id)
            if registry_id := entry["registry_id"]:
                self.registry_ids[modbus_id] = int(registry_id)
            config = self.sensor_configs.get(probe_name)
            if config is not None:
                config["bed_name"] = entry["bed_name"]

    async def _auto_register_sensor(self, modbus_id: int) -> bool:
        """Upsert a discovered Modbus address (unassigned) and wire it into
        the polling loop.

        Returns True on success (sensor is now wired into the polling loop),
        False on any failure (caller must NOT mark the modbus_id as discovered
        so the next scan retries)."""
        probe_name = self._probe_name(modbus_id)
        reader = SoilSensorReader(cast(str, self.rs485_port), modbus_id, self.rs485_baudrate)

        try:
            reader.connect()

            registry_id = await self.database.upsert_unassigned_probe(modbus_id)
            await self._wire_probe(modbus_id, registry_id, bed_name=None)

            logger.info(f"Auto-registered sensor: {probe_name} (Modbus ID: {modbus_id}, unassigned)")
            return True

        except Exception as e:
            logger.error(f"Failed to auto-register sensor Modbus ID {modbus_id}: {e}")
            try:
                reader.disconnect()
            except Exception as disc_err:
                logger.warning(f"Error disconnecting reader: {disc_err}")
            return False

    async def _polling_loop(self) -> None:
        """Poll all wired sensors at the configured interval."""
        polling_config = self.config.get_polling_config()
        interval = polling_config.get("interval_seconds", 5)

        while self.running:
            try:
                await self._poll_all_sensors()
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)

            await asyncio.sleep(interval)

    async def _poll_all_sensors(self) -> None:
        """Poll every wired probe.

        Measurements and the raw soil stream are written even while a probe
        is unassigned (empty bed metadata); assigned probes include their
        bed. The service never chooses a bed."""
        timestamp = datetime.now()

        for probe_name, config in self.sensor_configs.items():
            bed_name: str | None = config["bed_name"]
            room_name = "Flower Room"

            reader = self.sensor_readers.get(probe_name)
            if not reader:
                continue

            try:
                # Read all parameters
                readings = await asyncio.to_thread(reader.read_all_parameters)

                if readings:
                    # Store in database (also before assignment)
                    sensor_ids = self.sensor_ids.get(probe_name, {})
                    await self.database.store_measurements(sensor_ids, readings, timestamp)

                    # Write to Redis Stream (sensor:raw) with bed metadata
                    await self.redis_client.write_to_stream(
                        probe_name, readings, bed_name, room_name
                    )

                    # Publish to stable per-probe Redis state keys
                    await self.redis_client.publish_all_readings(
                        probe_name, readings, bed_name, room_name
                    )

                    logger.info(
                        f"Read {probe_name}: "
                        f"T={readings.get('temperature', 0):.1f}°C, "
                        f"H={readings.get('humidity', 0):.1f}%, "
                        f"EC={readings.get('ec', 0):.1f}µS/cm, "
                        f"pH={readings.get('ph', 0):.2f}"
                    )
                else:
                    logger.warning(f"Failed to read sensor: {probe_name}")

            except Exception as e:
                logger.error(f"Error polling sensor {probe_name}: {e}")
                # Try to reconnect
                try:
                    if (
                        reader.modbus is None
                        or not reader.modbus.ser
                        or not reader.modbus.ser.is_open
                    ):
                        reader.connect()
                except Exception as reconnect_error:
                    logger.error(f"Failed to reconnect sensor {probe_name}: {reconnect_error}")
