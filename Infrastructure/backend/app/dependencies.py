"""Shared dependencies for FastAPI routes."""

from __future__ import annotations

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.repositories.config_repository import ConfigRepository
from app.repositories.sensor_repository import SensorRepository

_db_manager: DatabaseManager | None = None
_config_loader: ConfigLoader | None = None
_sensor_repository: SensorRepository | None = None
_config_repository: ConfigRepository | None = None


def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_config_loader() -> ConfigLoader:
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


def get_sensor_repository() -> SensorRepository:
    global _sensor_repository
    if _sensor_repository is None:
        _sensor_repository = SensorRepository(db_manager=get_db_manager())
    return _sensor_repository


def get_config_repository() -> ConfigRepository:
    global _config_repository
    if _config_repository is None:
        loader = get_config_loader()
        _config_repository = ConfigRepository(config_path=loader.config_path)
    return _config_repository
