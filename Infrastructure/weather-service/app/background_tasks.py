"""Background tasks for polling weather API."""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from .config import ConfigLoader
from .database import DatabaseManager
from .weather_client import WeatherClient

logger = logging.getLogger(__name__)


class BackgroundTasks:
    """Manages background polling tasks for weather data."""
    
    def __init__(
        self,
        config: ConfigLoader,
        database: DatabaseManager,
        weather_client: WeatherClient
    ):
        """Initialize background tasks.
        
        Args:
            config: Configuration loader
            database: Database manager
            weather_client: Weather API client
        """
        self.config = config
        self.database = database
        self.weather_client = weather_client
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.sensor_ids: dict = {}
        self.device_id: Optional[int] = None
        
    async def start(self) -> None:
        """Start background polling task."""
        if self.running:
            logger.warning("Background tasks already running")
            return
        
        # Get configuration
        room_config = self.config.get_room_config()
        room_name = room_config['name']
        device_name = room_config['device_name']
        
        # Ensure database hierarchy exists
        room_id, device_id = await self.database.ensure_hierarchy(
            room_name, device_name
        )
        self.device_id = device_id
        
        # Register weather sensors
        self.sensor_ids = await self.database.register_weather_sensors(device_id)
        
        logger.info(f"Initialized weather service for {room_name} / {device_name}")
        logger.info(f"Registered {len(self.sensor_ids)} weather sensors")
        
        # Start polling task
        self.running = True
        self.task = asyncio.create_task(self._polling_loop())
        logger.info("Background weather polling task started")
    
    async def stop(self) -> None:
        """Stop background polling task."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        
        logger.info("Background weather polling task stopped")
    
    async def _polling_loop(self) -> None:
        """Main polling loop."""
        weather_config = self.config.get_weather_config()
        interval = weather_config.get('poll_interval', 900)  # Default 15 minutes
        
        # Poll immediately on start
        await self._poll_weather()
        
        while self.running:
            try:
                await asyncio.sleep(interval)
                if self.running:
                    await self._poll_weather()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)
                # Continue running even on error
                await asyncio.sleep(interval)
    
    async def _poll_weather(self) -> None:
        """Poll weather API and store data."""
        try:
            logger.info("Fetching weather data...")
            
            # Fetch METAR data
            weather_data = await self.weather_client.fetch_metar()
            
            if not weather_data:
                logger.warning("Failed to fetch weather data")
                return
            
            # Store measurements in database
            success = await self.database.store_weather_measurements(
                self.sensor_ids,
                weather_data
            )
            
            if success:
                # Log successful fetch
                temp = weather_data.get('temperature', 'N/A')
                rh = weather_data.get('relative_humidity', 'N/A')
                pressure = weather_data.get('pressure', 'N/A')
                wind_speed = weather_data.get('wind_speed', 'N/A')
                wind_dir = weather_data.get('wind_direction', 'N/A')
                
                logger.info(
                    f"Weather data stored: "
                    f"T={temp}°C, "
                    f"RH={rh}%, "
                    f"P={pressure}hPa, "
                    f"Wind={wind_speed}m/s @ {wind_dir}°"
                )
            else:
                logger.error("Failed to store weather measurements in database")
                
        except Exception as e:
            logger.error(f"Error polling weather: {e}", exc_info=True)












