"""Main FastAPI application for weather service."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.background_tasks import BackgroundTasks
from app.database import DatabaseManager
from app.routes import status, weather
from app.weather_client import WeatherClient
from shared.infra_logging import setup_structured_logging
from shared.lifespan import notify_started, notify_stopping
from shared.service_settings import ServiceSettings

# Configure structured logging
logger = setup_structured_logging(
    service_name="weather-service", log_level="INFO", console_output=True, json_format=True
)

settings: dict[str, Any] | None = None
database: DatabaseManager | None = None
weather_client: WeatherClient | None = None
background_tasks: BackgroundTasks | None = None
settings_refresh_task: asyncio.Task[None] | None = None


WEATHER_SETTING_KEYS = frozenset(
    {"station_icao", "poll_interval_seconds", "api_url", "room_name", "device_name"}
)


def _has_usable_weather_settings(values: dict[str, Any]) -> bool:
    return WEATHER_SETTING_KEYS.issubset(values)


async def _load_weather_settings(
    database_manager: DatabaseManager,
) -> tuple[Any, dict[str, Any]]:
    """Wait for a complete DB/cache snapshot; weather has no local defaults."""
    while True:
        try:
            pool = await database_manager._get_pool()
            values = await ServiceSettings.load(pool, "weather")
        except Exception as e:
            logger.warning(f"Unable to load weather settings: {e}")
        else:
            if _has_usable_weather_settings(values):
                return pool, values
            logger.warning(
                "Weather settings are incomplete; retrying until a usable snapshot loads"
            )
        await asyncio.sleep(10.0)


def _apply_weather_settings(values: dict[str, Any]) -> None:
    if settings is None or not _has_usable_weather_settings(values):
        logger.warning("Ignoring incomplete weather settings refresh")
        return
    settings.clear()
    settings.update(values)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global settings, database, weather_client, background_tasks, settings_refresh_task

    logger.info("Starting weather service...")
    try:
        logger.info("Initializing database...")
        database = DatabaseManager()
        await database.initialize()

        pool, settings = await _load_weather_settings(database)
        logger.info("Initializing weather client...")
        weather_client = WeatherClient(
            api_url=settings["api_url"], station_icao=settings["station_icao"]
        )

        logger.info("Initializing background tasks...")
        background_tasks = BackgroundTasks(settings, database, weather_client)
        await background_tasks.start()
        settings_refresh_task = asyncio.create_task(
            ServiceSettings.run_refresh(pool, "weather", _apply_weather_settings)
        )

        logger.info("Weather service started successfully")
        notify_started("weather-service", logger)

        yield

    except Exception as e:
        logger.error(f"Failed to start weather service: {e}", exc_info=True)
        raise
    finally:
        notify_stopping("weather-service", logger)

        if background_tasks:
            await background_tasks.stop()

        if settings_refresh_task:
            settings_refresh_task.cancel()
            try:
                await settings_refresh_task
            except asyncio.CancelledError:
                pass

        if weather_client:
            await weather_client.close()

        if database:
            await database.close()

        logger.info("Weather service stopped")


from shared.fastapi_helpers import docs_kwargs  # noqa: E402

# Create FastAPI app
app = FastAPI(
    title="Weather Service",
    description="METAR weather data service for CEA system",
    version="1.0.0",
    lifespan=lifespan,
    **docs_kwargs(),  # ENV=production closes /docs, /redoc, /openapi.json.
)

# CORS middleware (env-driven; unsafe '*' + credentials=True combo removed)
from shared.auth import APIKeyAuthMiddleware  # noqa: E402
from shared.middleware import setup_cors  # noqa: E402

setup_cors(app, service_name="weather-service")
# API-key gate for /api/*. No-op until CEA_API_KEY_REQUIRE=true.
app.add_middleware(APIKeyAuthMiddleware)


# Dependency injection functions


def get_database() -> DatabaseManager:
    """Get database manager."""
    if database is None:
        raise RuntimeError("Database not initialized")
    return database


# Override route dependencies
app.dependency_overrides[status.get_database] = get_database
app.dependency_overrides[weather.get_database] = get_database

# Register routes
app.include_router(status.router)
app.include_router(weather.router, prefix="/weather", tags=["weather"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8003)
