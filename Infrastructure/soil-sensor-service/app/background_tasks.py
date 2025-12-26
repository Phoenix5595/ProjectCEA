"""Background tasks for polling soil sensors."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from .config import ConfigLoader
from .database import DatabaseManager
from .redis_client import RedisClient
from .soil_sensor_reader import SoilSensorReader

logger = logging.getLogger(__name__)


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
        self.sensor_readers: Dict[str, SoilSensorReader] = {}
        self.sensor_configs: List[Dict[str, Any]] = []
        self.sensor_ids: Dict[str, Dict[str, int]] = {}  # sensor_name -> {type: sensor_id}
        
    async def start(self) -> None:
        """Start background polling task."""
        if self.running:
            logger.warning("Background tasks already running")
            return
        
        # Load sensor configurations
        self.sensor_configs = self.config.get_sensors()
        if not self.sensor_configs:
            logger.warning("No sensors configured")
            return
        
        # Initialize sensor readers and register in database
        rs485_config = self.config.get_rs485_config()
        port = rs485_config['port']
        baudrate = rs485_config.get('baudrate', 9600)
        
        for sensor_config in self.sensor_configs:
            sensor_name = sensor_config['name']
            modbus_id = sensor_config['modbus_id']
            bed_name = sensor_config['bed_name']
            room_name = sensor_config.get('room_name', 'Flower Room')
            
            # Create sensor reader
            reader = SoilSensorReader(port, modbus_id, baudrate)
            self.sensor_readers[sensor_name] = reader
            
            # Ensure database hierarchy exists
            room_id, rack_id = await self.database.ensure_hierarchy(
                room_name, bed_name
            )
            
            # Register sensor device and get sensor IDs
            device_id, sensor_ids = await self.database.register_sensor_device(
                rack_id, sensor_name, modbus_id, bed_name
            )
            self.sensor_ids[sensor_name] = sensor_ids
            
            logger.info(f"Initialized sensor: {sensor_name} (Modbus ID: {modbus_id}, Bed: {bed_name})")
        
        # Connect all sensor readers
        try:
            for reader in self.sensor_readers.values():
                reader.connect()
            logger.info("Connected to all soil sensors")
        except Exception as e:
            logger.error(f"Failed to connect to sensors: {e}")
            # Continue anyway, will retry in polling loop
        
        # Start polling task
        self.running = True
        self.task = asyncio.create_task(self._polling_loop())
        logger.info("Background polling task started")
    
    async def stop(self) -> None:
        """Stop background polling task."""
        self.running = False
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
        
        logger.info("Background polling task stopped")
    
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

