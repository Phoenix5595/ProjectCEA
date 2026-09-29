"""Weather data routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.database import DatabaseManager
from app.weather_client import WeatherClient

router = APIRouter()

CITY_NAME = "Québec City"


def degrees_to_cardinal(degrees: float | None) -> str | None:
    """Convert wind direction degrees to cardinal direction with triple-letter precision.

    Uses 16-point compass rose:
    N, NNE, NE, ENE, E, ESE, SE, SSE,
    S, SSW, SW, WSW, W, WNW, NW, NNW
    """
    if degrees is None:
        return None

    directions = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]

    index = round(degrees / 22.5) % 16
    return directions[index]


# Dependency injection (will be overridden in main.py)
def get_database() -> DatabaseManager:
    """Get database manager."""
    raise NotImplementedError("Dependency not injected")


def get_weather_client() -> WeatherClient:
    """Get weather client."""
    raise NotImplementedError("Dependency not injected")


@router.get("/latest")
async def get_latest_weather(db: DatabaseManager = Depends(get_database)) -> dict[str, Any]:
    """Get latest weather measurements."""
    try:
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            # Get latest measurements for the weather sensors.
            # Lateral latest-per-sensor uses ``idx_measurement_sensor_time``
            # directly; the previous DISTINCT ON over the full join scanned
            # every measurement chunk (tens of seconds) before deduplication.
            rows = await conn.fetch("""
                SELECT s.name AS sensor_name, m.value, m.time, s.unit
                FROM sensor s
                LEFT JOIN LATERAL (
                    SELECT m.value, m.time
                    FROM measurement m
                    WHERE m.sensor_id = s.sensor_id
                    ORDER BY m.time DESC
                    LIMIT 1
                ) m ON true
                WHERE s.name LIKE 'outside_%'
                ORDER BY s.sensor_id
            """)

            weather_data = {}
            timestamp = None
            wind_direction_degrees = None

            for row in rows:
                sensor_name = row["sensor_name"]
                key = sensor_name.replace("outside_", "")
                weather_data[key] = {"value": row["value"], "unit": row["unit"]}

                if key == "wind_direction":
                    wind_direction_degrees = row["value"]

                if timestamp is None or row["time"] > timestamp:
                    timestamp = row["time"]

            wind_cardinal = degrees_to_cardinal(wind_direction_degrees)

            return {
                "timestamp": timestamp.isoformat() if timestamp else None,
                "city": CITY_NAME,
                "data": weather_data,
                "wind_cardinal": wind_cardinal,
            }
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Error fetching weather data: {e!r}")
        return {"error": str(e) or repr(e)}


@router.post("/fetch")
async def fetch_weather_now(
    db: DatabaseManager = Depends(get_database),
    weather_client: WeatherClient = Depends(get_weather_client),
) -> dict[str, Any]:
    """Manually trigger a weather data fetch."""
    try:
        weather_data = await weather_client.fetch_metar()
        if weather_data:
            return {"status": "success", "data": weather_data}
        else:
            return {"status": "error", "message": "Failed to fetch weather data"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
