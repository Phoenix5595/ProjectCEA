"""Background tasks for polling weather API."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from shared.db_errors import is_transient_db_error
from shared.infra_logging import get_logger
from shared.replay_buffer import ReplayBuffer

from .database import DatabaseManager
from .weather_client import WeatherClient

logger = get_logger(__name__)


class BackgroundTasks:
    """Manages background polling tasks for weather data."""

    def __init__(
        self, settings: dict[str, Any], database: DatabaseManager, weather_client: WeatherClient
    ):
        """Initialize background tasks.

        Args:
            settings: DB/cache-backed weather settings snapshot
            database: Database manager
            weather_client: Weather API client
        """
        self.settings = settings
        self.database = database
        self.weather_client = weather_client
        self.running = False
        self.task: asyncio.Task | None = None
        self.sensor_ids: dict = {}
        self.device_id: int | None = None
        self._pending: ReplayBuffer[dict[str, Any]] = ReplayBuffer(8, name="weather_measurements")

    async def start(self) -> None:
        """Start background polling task."""
        if self.running:
            logger.warning("Background tasks already running")
            return

        room_name = self.settings["room_name"]
        device_name = self.settings["device_name"]

        # Ensure database hierarchy exists
        room_id, device_id = await self.database.ensure_hierarchy(room_name, device_name)
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
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

        logger.info("Background weather polling task stopped")

    async def _polling_loop(self) -> None:
        """Poll immediately, using current DB-backed settings on every cycle."""
        while self.running:
            interval = self.settings["poll_interval_seconds"]
            try:
                self.weather_client.update_settings(
                    api_url=self.settings["api_url"],
                    station_icao=self.settings["station_icao"],
                )
                await self._poll_weather()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in polling loop: {e}", exc_info=True)
            await asyncio.sleep(interval)

    async def _store_weather_data(self, weather_data: dict[str, Any]) -> bool:
        success = await self.database.store_weather_measurements(self.sensor_ids, weather_data)
        if not success:
            logger.error("Failed to store weather measurements in database")
            return False

        temp = weather_data.get("temperature", "N/A")
        rh = weather_data.get("relative_humidity", "N/A")
        pressure = weather_data.get("pressure", "N/A")
        wind_speed = weather_data.get("wind_speed", "N/A")
        wind_dir = weather_data.get("wind_direction", "N/A")
        logger.info(
            f"Weather data stored: "
            f"T={temp}°C, "
            f"RH={rh}%, "
            f"P={pressure}hPa, "
            f"Wind={wind_speed}m/s @ {wind_dir}°"
        )
        return True

    async def _poll_weather(self) -> None:
        """Poll weather API and store data."""
        try:
            for pending in self._pending.peek(8):
                try:
                    stored = await self._store_weather_data(pending)
                except Exception as e:
                    if is_transient_db_error(e):
                        logger.error(f"Error replaying weather measurements: {e}")
                        break
                    logger.error(f"Dropping weather report after permanent database error: {e}")
                    self._pending.consume(1)
                    continue
                if not stored:
                    break
                self._pending.consume(1)

            logger.info("Fetching weather data...")
            weather_data = await self.weather_client.fetch_metar()
            if not weather_data:
                logger.warning("Failed to fetch weather data")
                return

            if self._pending:
                self._pending.offer([weather_data])
                return

            try:
                stored = await self._store_weather_data(weather_data)
            except Exception as e:
                if is_transient_db_error(e):
                    logger.warning(f"Database unavailable for weather measurements; buffering: {e}")
                    self._pending.offer([weather_data])
                else:
                    logger.error(f"Dropping weather report after permanent database error: {e}")
            else:
                if not stored:
                    self._pending.offer([weather_data])

        except Exception as e:
            logger.error(f"Error polling weather: {e}", exc_info=True)
