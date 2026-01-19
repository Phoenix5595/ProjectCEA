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


class LocationResponse(BaseModel):
    """Available location information."""

    name: str
    type: str
    clusters: list[str]


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
    location: str | None = None
    cluster: str | None = None
    stale: bool = False
    age_seconds: float | None = None


class LiveSnapshotResponse(BaseModel):
    """Live snapshot response with consistent timestamp."""

    ts: int  # Unix timestamp in seconds
    ts_iso: str  # ISO format timestamp
    values: dict[str, LiveSensorValue]
