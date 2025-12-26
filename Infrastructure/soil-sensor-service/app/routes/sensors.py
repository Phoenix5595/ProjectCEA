"""Sensor data routes."""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional, Dict, Any
from datetime import datetime
from app.database import DatabaseManager
from app.redis_client import RedisClient

router = APIRouter()

# Dependency injection (will be overridden in main.py)
def get_database() -> DatabaseManager:
    """Get database manager."""
    raise NotImplementedError("Dependency not injected")

def get_redis_client() -> RedisClient:
    """Get Redis client."""
    raise NotImplementedError("Dependency not injected")


@router.get("/api/sensors")
async def list_sensors(db: DatabaseManager = Depends(get_database)) -> List[Dict[str, Any]]:
    """List all configured soil sensors."""
    try:
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    s.sensor_id,
                    s.name,
                    s.unit,
                    s.data_type,
                    d.name as device_name,
                    r.name as bed_name,
                    rm.name as room_name
                FROM sensor s
                JOIN device d ON s.device_id = d.device_id
                LEFT JOIN rack r ON d.rack_id = r.rack_id
                LEFT JOIN room rm ON r.room_id = rm.room_id
                WHERE d.type = 'RS485 Soil Sensor'
                ORDER BY s.sensor_id
            """)
            
            sensors = []
            for row in rows:
                sensors.append({
                    "sensor_id": row['sensor_id'],
                    "name": row['name'],
                    "unit": row['unit'],
                    "data_type": row['data_type'],
                    "device_name": row['device_name'],
                    "bed_name": row['bed_name'],
                    "room_name": row['room_name']
                })
            
            return sensors
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing sensors: {str(e)}")


@router.get("/api/sensors/{sensor_id}/latest")
async def get_latest_reading(
    sensor_id: int,
    db: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get latest reading for a sensor."""
    try:
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    m.time,
                    m.value,
                    m.status,
                    s.name,
                    s.unit,
                    s.data_type
                FROM measurement m
                JOIN sensor s ON m.sensor_id = s.sensor_id
                WHERE s.sensor_id = $1
                ORDER BY m.time DESC
                LIMIT 1
            """, sensor_id)
            
            if not row:
                raise HTTPException(status_code=404, detail="Sensor not found or no readings")
            
            return {
                "sensor_id": sensor_id,
                "sensor_name": row['name'],
                "unit": row['unit'],
                "data_type": row['data_type'],
                "value": float(row['value']),
                "time": row['time'].isoformat(),
                "status": row['status']
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting latest reading: {str(e)}")


@router.get("/api/sensors/{sensor_id}/readings")
async def get_readings(
    sensor_id: int,
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: DatabaseManager = Depends(get_database)
) -> Dict[str, Any]:
    """Get historical readings for a sensor."""
    try:
        pool = await db._get_pool()
        async with pool.acquire() as conn:
            # Build query
            query = """
                SELECT 
                    m.time,
                    m.value,
                    m.status
                FROM measurement m
                WHERE m.sensor_id = $1
            """
            params = [sensor_id]
            param_idx = 2
            
            if start_time:
                query += f" AND m.time >= ${param_idx}"
                params.append(start_time)
                param_idx += 1
            
            if end_time:
                query += f" AND m.time <= ${param_idx}"
                params.append(end_time)
                param_idx += 1
            
            query += " ORDER BY m.time DESC LIMIT $" + str(param_idx)
            params.append(limit)
            
            rows = await conn.fetch(query, *params)
            
            readings = []
            for row in rows:
                readings.append({
                    "time": row['time'].isoformat(),
                    "value": float(row['value']),
                    "status": row['status']
                })
            
            return {
                "sensor_id": sensor_id,
                "count": len(readings),
                "readings": readings
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting readings: {str(e)}")


@router.get("/api/sensors/live")
async def get_live_readings(
    redis_client: RedisClient = Depends(get_redis_client)
) -> Dict[str, Any]:
    """Get current live sensor values from Redis state keys.
    
    Returns all soil sensor values currently in Redis.
    """
    try:
        # Get all sensor values from Redis
        # Note: This requires access to Redis state keys
        # For now, return a message indicating this endpoint needs Redis client access
        # The actual implementation would read from sensor:* keys
        
        return {
            "message": "Live readings endpoint - requires Redis state key access",
            "note": "This endpoint should read from sensor:* Redis keys"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting live readings: {str(e)}")

