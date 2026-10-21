"""Pydantic models for API request/response validation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    data: list[DataPoint]
    unit: str


class WebSocketMessage(BaseModel):
    """WebSocket message format."""

    type: str  # "sensor_update", etc.
    location: str
    cluster: str
    sensor_type: str
    timestamp: datetime
    value: float
    unit: str
