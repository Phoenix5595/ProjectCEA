"""Pydantic models for API request/response validation."""
from __future__ import annotations

from shared.logging import get_logger
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


class LocationResponse(BaseModel):
    """Available location information."""
    name: str
    type: str
    clusters: List[str]


class WebSocketMessage(BaseModel):
    """WebSocket message format."""
    type: str  # "sensor_update", etc.
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

