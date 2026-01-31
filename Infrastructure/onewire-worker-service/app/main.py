"""FastAPI app for 1-Wire reader service."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.logging import setup_structured_logging

from .background_tasks import BackgroundTasks
from .config import ConfigLoader
from .onewire_reader import read_temperature_c
from .redis_client import RedisClient

logger = setup_structured_logging(
    service_name="onewire-worker", log_level="INFO", console_output=True, json_format=True
)

config: ConfigLoader | None = None
redis_client: RedisClient | None = None
background_tasks: BackgroundTasks | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, redis_client, background_tasks
    logger.info("Starting onewire-worker...")
    try:
        config = ConfigLoader()
        redis_client = RedisClient()
        await redis_client.connect()
        background_tasks = BackgroundTasks(config, redis_client)
        await background_tasks.start()
        logger.info("onewire-worker started")
        yield
    finally:
        if background_tasks:
            await background_tasks.stop()
        if redis_client:
            await redis_client.close()
        logger.info("onewire-worker stopped")


app = FastAPI(title="1-Wire Reader Service", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "onewire-worker"}


@app.get("/readings")
async def readings():
    """Current readings from configured devices (for debugging)."""
    if not config:
        return {}
    out = {}
    for device_id, sensor_name in config.get_devices().items():
        temp = read_temperature_c(device_id)
        out[sensor_name] = temp
    return out


@app.get("/")
async def root():
    return {"service": "1-Wire Reader Service", "version": "1.0.0", "status": "running"}
