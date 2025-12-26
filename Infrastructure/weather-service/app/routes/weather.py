"""Weather data routes."""
from fastapi import APIRouter, Depends
from typing import Dict, Any, Optional
from datetime import datetime
from app.database import DatabaseManager
from app.weather_client import WeatherClient

router = APIRouter()

# Dependency injection (will be overridden in main.py)
def get_database() -> DatabaseManager:
    """Get database manager."""
    raise NotImplementedError("Dependency not injected")


def get_weather_client() -> WeatherClient:
    """Get weather client."""
    raise NotImplementedError("Dependency not injected")


@router.get("/latest")
async def get_latest_weather(
    db: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get latest weather measurements."""
    try:
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            # Get latest measurements for all weather sensors
            rows = await conn.fetch("""
                SELECT 
                    s.name as sensor_name,
                    m.value,
                    m.time,
                    s.unit
                FROM measurement m
                JOIN sensor s ON m.sensor_id = s.sensor_id
                WHERE s.name LIKE 'outside_%'
                AND m.time = (
                    SELECT MAX(time) 
                    FROM measurement 
                    WHERE sensor_id = m.sensor_id
                )
                ORDER BY s.name
            """)
            
            weather_data = {}
            timestamp = None
            for row in rows:
                sensor_name = row['sensor_name']
                # Remove 'outside_' prefix for response
                key = sensor_name.replace('outside_', '')
                weather_data[key] = {
                    'value': row['value'],
                    'unit': row['unit']
                }
                if timestamp is None:
                    timestamp = row['time']
            
            return {
                'timestamp': timestamp.isoformat() if timestamp else None,
                'data': weather_data
            }
    except Exception as e:
        return {"error": str(e)}


@router.post("/fetch")
async def fetch_weather_now(
    db: DatabaseManager = Depends(get_database),
    weather_client: WeatherClient = Depends(get_weather_client)
) -> Dict[str, Any]:
    """Manually trigger a weather data fetch."""
    try:
        weather_data = await weather_client.fetch_metar()
        if weather_data:
            return {
                "status": "success",
                "data": weather_data
            }
        else:
            return {
                "status": "error",
                "message": "Failed to fetch weather data"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }












