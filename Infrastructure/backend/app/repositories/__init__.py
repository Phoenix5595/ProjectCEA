"""Repository pattern for backend data access."""

from __future__ import annotations

from app.repositories.base import BaseRepository
from app.repositories.sensor_repository import SensorRepository

__all__ = ["BaseRepository", "SensorRepository"]
