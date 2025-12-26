"""Pydantic models for API request/response validation."""
from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field


class DataPoint(BaseModel):
    """Single sensor data point."""
    timestamp: datetime
    value: float
    unit: str


class SensorDataResponse(BaseModel):
    """Response containing sensor data points."""
    sensor_type: str
    location: str
    cluster: str
    data: List[DataPoint]
    unit: str


class StatisticsResponse(BaseModel):
    """Statistics for a sensor over a time range."""
    sensor_type: str
    location: str
    cluster: str
    min: float
    max: float
    avg: float
    std_dev: float
    unit: str


class LocationResponse(BaseModel):
    """Available location information."""
    name: str
    type: str
    clusters: List[str]


class WebSocketMessage(BaseModel):
    """WebSocket message format."""
    type: str  # "sensor_update", "statistics_update", etc.
    location: str
    cluster: str
    sensor_type: str
    timestamp: datetime
    value: float
    unit: str


class LiveSensorValue(BaseModel):
    """Live sensor value in snapshot."""
    value: float
    unit: str
    sensor: str
    location: Optional[str] = None
    cluster: Optional[str] = None
    stale: bool = False
    age_seconds: Optional[float] = None


class LiveSnapshotResponse(BaseModel):
    """Live snapshot response with consistent timestamp."""
    ts: int  # Unix timestamp in seconds
    ts_iso: str  # ISO format timestamp
    values: Dict[str, LiveSensorValue]

