"""Background tasks for polling soil sensors."""
from __future__ import annotations

from shared.logging import get_logger
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from .config import ConfigLoader
from .database import DatabaseManager
from .redis_client import RedisClient
from .soil_sensor_reader import SoilSensorReader
from .modbus_rtu import ModbusRTU

logger = get_logger(__name__)


class BackgroundTasks:
    """Manages background polling tasks for soil sensors."""
    
    def __init__(
        self,
        config: ConfigLoader,
        database: DatabaseManager,
        redis_client: RedisClient
    ):
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
        self.task: Optional[asyncio.Task] = None
        self.discovery_task: Optional[asyncio.Task] = None
        self.sensor_readers: Dict[str, SoilSensorReader] = {}
        self.sensor_configs: List[Dict[str, Any]] = []
        self.sensor_ids: Dict[str, Dict[str, int]] = {}  # sensor_name -> {type: sensor_id}
        self.discovered_modbus_ids: set = set()  # Track discovered Modbus IDs
        self.rs485_port: str = None
        self.rs485_baudrate: int = 9600
        
    async def start(self) -> None:
        """Start background polling task."""
        if self.running:
            logger.warning("Background tasks already running")
            return
        
        # Get RS485 configuration
        rs485_config = self.config.get_rs485_config()
        self.rs485_port = rs485_config['port']
        self.rs485_baudrate = rs485_config.get('baudrate', 9600)
        
        # Load sensor configurations from config file (if any)
        self.sensor_configs = self.config.get_sensors()
        
        # Initialize any pre-configured sensors
        if self.sensor_configs:
            for sensor_config in self.sensor_configs:
                sensor_name = sensor_config['name']
                modbus_id = sensor_config['modbus_id']
                bed_name = sensor_config['bed_name']
                room_name = sensor_config.get('room_name', 'Flower Room')
                
                # Create sensor reader
                reader = SoilSensorReader(self.rs485_port, modbus_id, self.rs485_baudrate)
                self.sensor_readers[sensor_name] = reader
                self.discovered_modbus_ids.add(modbus_id)
                
                # Ensure database hierarchy exists
                room_id, rack_id = await self.database.ensure_hierarchy(
                    room_name, bed_name
                )
                
                # Register sensor device and get sensor IDs
                device_id, sensor_ids = await self.database.register_sensor_device(
                    rack_id, sensor_name, modbus_id, bed_name
                )
                self.sensor_ids[sensor_name] = sensor_ids
                
                logger.info(f"Initialized configured sensor: {sensor_name} (Modbus ID: {modbus_id}, Bed: {bed_name})")
        
        # Connect all sensor readers
        try:
            for reader in self.sensor_readers.values():
                reader.connect()
            if self.sensor_readers:
                logger.info("Connected to all configured soil sensors")
        except Exception as e:
            logger.error(f"Failed to connect to sensors: {e}")
            # Continue anyway, will retry in polling loop
        
        # Start discovery and polling tasks
        self.running = True
        self.discovery_task = asyncio.create_task(self._discovery_loop())
        self.task = asyncio.create_task(self._polling_loop())
        logger.info("Background polling and discovery tasks started")
    
    async def stop(self) -> None:
        """Stop background polling task."""
        self.running = False
        if self.discovery_task:
            self.discovery_task.cancel()
            try:
                await self.discovery_task
            except asyncio.CancelledError:
                pass
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        # Disconnect all sensor readers
        for reader in self.sensor_readers.values():
            try:
                reader.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting sensor: {e}")
        
        logger.info("Background polling and discovery tasks stopped")
    
    async def _discovery_loop(self) -> None:
        """Periodically scan bus for new sensors."""
        polling_config = self.config.get_polling_config()
        discovery_interval = polling_config.get('discovery_interval_seconds', 30)  # Default 30 seconds
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
        """Scan Modbus bus for sensors and auto-register new ones."""
        try:
            # Create temporary Modbus connection for scanning
            temp_modbus = ModbusRTU(self.rs485_port, self.rs485_baudrate, timeout=0.5)
            temp_modbus.connect()
            
            found_new = False
            
            for modbus_id in range(start_id, end_id + 1):
                # Skip if already discovered
                if modbus_id in self.discovered_modbus_ids:
                    continue
                
                # Try to read first register to detect sensor
                try:
                    registers = temp_modbus.read_holding_registers(modbus_id, 0x0000, 1)
                    if registers is not None:
                        # Found a new sensor!
                        logger.info(f"Discovered new sensor at Modbus ID {modbus_id}")
                        await self._auto_register_sensor(modbus_id)
                        found_new = True
                        self.discovered_modbus_ids.add(modbus_id)
                except Exception:
                    # Sensor not found or error, continue scanning
                    pass
            
            temp_modbus.disconnect()
            
            if not found_new and not self.discovered_modbus_ids:
                # No sensors found yet, log periodically (every 10th scan = 5 minutes)
                if hasattr(self, '_scan_count'):
                    self._scan_count += 1
                else:
                    self._scan_count = 1
                
                if self._scan_count % 10 == 0:
                    logger.info("No sensors found on bus yet. Continuing to scan...")
            
        except Exception as e:
            logger.error(f"Error scanning bus: {e}")
            # Don't raise, keep trying
    
    async def _auto_register_sensor(self, modbus_id: int) -> None:
        """Auto-register a newly discovered sensor."""
        # Generate default name and bed assignment
        sensor_count = len(self.sensor_readers) + 1
        
        # Alternate between Front Bed and Back Bed
        bed_names = ["Front Bed", "Back Bed"]
        bed_name = bed_names[(sensor_count - 1) % len(bed_names)]
        room_name = "Flower Room"
        
        sensor_name = f"soil_sensor_{modbus_id}"
        
        # Create sensor reader
        reader = SoilSensorReader(self.rs485_port, modbus_id, self.rs485_baudrate)
        
        try:
            # Connect to sensor
            reader.connect()
            
            # Ensure database hierarchy exists
            room_id, rack_id = await self.database.ensure_hierarchy(
                room_name, bed_name
            )
            
            # Register sensor device and get sensor IDs
            device_id, sensor_ids = await self.database.register_sensor_device(
                rack_id, sensor_name, modbus_id, bed_name
            )
            
            # Add to tracking
            self.sensor_readers[sensor_name] = reader
            self.sensor_ids[sensor_name] = sensor_ids
            
            logger.info(
                f"Auto-registered sensor: {sensor_name} "
                f"(Modbus ID: {modbus_id}, Bed: {bed_name})"
            )
            
        except Exception as e:
            logger.error(f"Failed to auto-register sensor Modbus ID {modbus_id}: {e}")
            try:
                reader.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting reader: {e}")
    
    async def _polling_loop(self) -> None:
        """Main polling loop."""
        polling_config = self.config.get_polling_config()
        interval = polling_config.get('interval_seconds', 5)
        
        while self.running:
            try:
                await self._poll_all_sensors()
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)
            
            await asyncio.sleep(interval)
    
    async def _poll_all_sensors(self) -> None:
        """Poll all configured sensors."""
        timestamp = datetime.now()
        
        for sensor_config in self.sensor_configs:
            sensor_name = sensor_config['name']
            bed_name = sensor_config['bed_name']
            room_name = sensor_config.get('room_name', 'Flower Room')
            
            reader = self.sensor_readers.get(sensor_name)
            if not reader:
                continue
            
            try:
                # Read all parameters
                readings = reader.read_all_parameters()
                
                if readings:
                    # Store in database
                    sensor_ids = self.sensor_ids.get(sensor_name, {})
                    await self.database.store_measurements(
                        sensor_ids, readings, timestamp
                    )
                    
                    # Write to Redis Stream (sensor:raw)
                    await self.redis_client.write_to_stream(
                        sensor_name, readings, bed_name, room_name
                    )
                    
                    # Publish to Redis state keys
                    await self.redis_client.publish_all_readings(
                        sensor_name, readings, bed_name, room_name
                    )
                    
                    logger.info(
                        f"Read {sensor_name}: "
                        f"T={readings.get('temperature', 0):.1f}°C, "
                        f"H={readings.get('humidity', 0):.1f}%, "
                        f"EC={readings.get('ec', 0):.1f}µS/cm, "
                        f"pH={readings.get('ph', 0):.2f}"
                    )
                else:
                    logger.warning(f"Failed to read sensor: {sensor_name}")
                    
            except Exception as e:
                logger.error(f"Error polling sensor {sensor_name}: {e}")
                # Try to reconnect
                try:
                    if reader.modbus is None or not reader.modbus.ser or not reader.modbus.ser.is_open:
                        reader.connect()
                except Exception as reconnect_error:
                    logger.error(f"Failed to reconnect sensor {sensor_name}: {reconnect_error}")

