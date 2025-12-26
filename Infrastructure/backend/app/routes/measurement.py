"""Measurement ingestion API routes."""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional
from pydantic import BaseModel, Field
import asyncpg
import os
import logging
from app.database import DatabaseManager
from app.dependencies import get_db_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["measurement"])


class MeasurementRequest(BaseModel):
    """Request model for measurement ingestion."""
    time: datetime = Field(..., description="Measurement timestamp (ISO 8601)")
    sensor_id: int = Field(..., description="Sensor ID from sensor table", gt=0)
    value: float = Field(..., description="Sensor reading value")
    status: Optional[str] = Field(None, description="Optional status indicator")


class MeasurementResponse(BaseModel):
    """Response model for measurement ingestion."""
    measurement_id: Optional[int] = None
    time: datetime
    sensor_id: int
    value: float
    status: Optional[str] = None
    message: str


@router.post("/measurement", response_model=MeasurementResponse, status_code=201)
async def create_measurement(
    measurement: MeasurementRequest,
    db_manager: DatabaseManager = Depends(get_db_manager)
):
    """Create a new measurement record.
    
    This endpoint allows external systems to ingest sensor measurements
    directly into the normalized measurement table.
    
    Args:
        measurement: Measurement data (time, sensor_id, value, status)
        db_manager: Database manager (injected)
    
    Returns:
        Created measurement record with confirmation
    
    Raises:
        HTTPException: If sensor_id doesn't exist or database error occurs
    """
    try:
        pool = await db_manager._get_pool()
        async with pool.acquire() as conn:
            # Verify sensor exists
            sensor = await conn.fetchrow(
                "SELECT sensor_id, name FROM sensor WHERE sensor_id = $1",
                measurement.sensor_id
            )
            
            if not sensor:
                raise HTTPException(
                    status_code=404,
                    detail=f"Sensor with ID {measurement.sensor_id} not found"
                )
            
            # Insert measurement
            # Note: measurement table has composite primary key (time, sensor_id)
            # Use ON CONFLICT to handle duplicate timestamps gracefully
            result = await conn.fetchrow("""
                INSERT INTO measurement (time, sensor_id, value, status)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (time, sensor_id) 
                DO UPDATE SET value = EXCLUDED.value, status = EXCLUDED.status
                RETURNING time, sensor_id, value, status
            """, measurement.time, measurement.sensor_id, measurement.value, measurement.status)
            
            logger.info(f"Created measurement: sensor_id={measurement.sensor_id}, "
                       f"value={measurement.value}, time={measurement.time}")
            
            return MeasurementResponse(
                time=result['time'],
                sensor_id=result['sensor_id'],
                value=result['value'],
                status=result['status'],
                message=f"Measurement recorded for sensor {sensor['name']} (ID: {measurement.sensor_id})"
            )
    
    except HTTPException:
        raise
    except asyncpg.exceptions.ForeignKeyViolationError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sensor_id: {measurement.sensor_id} (foreign key violation)"
        )
    except Exception as e:
        logger.error(f"Error creating measurement: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/measurement/sensor/{sensor_id}")
async def get_measurements_by_sensor(
    sensor_id: int,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = 100,
    db_manager: DatabaseManager = Depends(get_db_manager)
):
    """Get measurements for a specific sensor.
    
    Args:
        sensor_id: Sensor ID
        start_time: Optional start time filter
        end_time: Optional end time filter
        limit: Maximum number of results (default: 100, max: 1000)
        db_manager: Database manager (injected)
    
    Returns:
        List of measurements
    """
    if limit > 1000:
        limit = 1000
    
    try:
        pool = await db_manager._get_pool()
        async with pool.acquire() as conn:
            # Verify sensor exists
            sensor = await conn.fetchrow(
                "SELECT sensor_id, name FROM sensor WHERE sensor_id = $1",
                sensor_id
            )
            
            if not sensor:
                raise HTTPException(
                    status_code=404,
                    detail=f"Sensor with ID {sensor_id} not found"
                )
            
            # Build query
            query = "SELECT time, sensor_id, value, status FROM measurement WHERE sensor_id = $1"
            params = [sensor_id]
            param_idx = 2
            
            if start_time:
                query += f" AND time >= ${param_idx}"
                params.append(start_time)
                param_idx += 1
            
            if end_time:
                query += f" AND time <= ${param_idx}"
                params.append(end_time)
                param_idx += 1
            
            query += " ORDER BY time DESC LIMIT $" + str(param_idx)
            params.append(limit)
            
            rows = await conn.fetch(query, *params)
            
            return {
                "sensor_id": sensor_id,
                "sensor_name": sensor['name'],
                "count": len(rows),
                "measurements": [
                    {
                        "time": row['time'].isoformat(),
                        "value": row['value'],
                        "status": row['status']
                    }
                    for row in rows
                ]
            }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting measurements: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

