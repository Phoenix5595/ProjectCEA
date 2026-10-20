"""Background polling of 1-Wire probes and Redis publish."""

from __future__ import annotations

import asyncio

import asyncpg

from shared.infra_logging import get_logger
from shared.service_settings import ServiceSettings

from .config import ConfigLoader
from .onewire_reader import read_temperature_c
from .redis_client import RedisClient

logger = get_logger(__name__)


class BackgroundTasks:
    """Poll 1-Wire devices and write to Redis."""

    def __init__(
        self, config: ConfigLoader, redis_client: RedisClient, settings_pool: asyncpg.Pool
    ) -> None:
        self.config = config
        self.redis_client = redis_client
        self.settings_pool = settings_pool
        self.running = False
        self._task: asyncio.Task | None = None
        self._settings_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._settings_task = asyncio.create_task(
            ServiceSettings.run_refresh(
                self.settings_pool,
                "onewire",
                self.config.apply_service_settings,
                fallback=self.config.get_service_settings_fallback(),
            )
        )
        self._task = asyncio.create_task(self._loop())
        logger.info("1-Wire polling started")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._settings_task:
            self._settings_task.cancel()
            try:
                await self._settings_task
            except asyncio.CancelledError:
                pass
        logger.info("1-Wire polling stopped")

    async def _loop(self) -> None:
        devices = self.config.get_devices()
        if not devices:
            logger.warning("No devices in config; polling idle")
        while self.running:
            interval = self.config.get_polling_interval_seconds()
            try:
                for device_id, sensor_name in devices.items():
                    temp = await asyncio.to_thread(read_temperature_c, device_id)
                    if temp is not None and self.redis_client.redis_enabled:
                        await self.redis_client.set_sensor_value(sensor_name, temp)
                        logger.debug(f"{sensor_name}={temp}°C")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Poll error: {e}")
                await asyncio.sleep(interval)
