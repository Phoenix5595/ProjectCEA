"""Configuration loader for the weather service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from shared.config import YamlConfigLoader


class ConfigLoader(YamlConfigLoader):
    """Loads and parses ``weather_config.yaml``."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            config_path,
            search_paths=[
                Path(__file__).parent.parent / "weather_config.yaml",
                Path("/home/antoine/ProjectCEA/Infrastructure/weather-service/weather_config.yaml"),
            ],
        )

    def get_weather_config(self) -> dict[str, Any]:
        """Get weather API configuration."""
        return self._config.get(
            "weather",
            {
                "station_icao": "CYUL",
                "poll_interval": 900,
                "api_url": "https://aviationweather.gov/api/data/metar",
            },
        )

    def get_database_config(self) -> dict[str, Any]:
        """Get database configuration.

        ``POSTGRES_PASSWORD`` from the environment overrides the YAML value
        if present (and is required if YAML doesn't carry one). This is
        intentionally service-local — the shared base loader stays mechanical.
        """
        db_config = self._config.get("database", {})
        password = os.getenv("POSTGRES_PASSWORD")
        if password:
            db_config["password"] = password
        elif "password" not in db_config:
            raise ValueError(
                "POSTGRES_PASSWORD environment variable or database.password "
                "in config file is required"
            )
        return {
            "host": db_config.get("host", "localhost"),
            "database": db_config.get("database", "cea_sensors"),
            "user": db_config.get("user", "cea_user"),
            "password": db_config.get("password"),
            "port": db_config.get("port", 5432),
        }

    def get_room_config(self) -> dict[str, Any]:
        """Get room and device configuration."""
        return self._config.get("room", {"name": "Outside", "device_name": "Weather Station YUL"})
