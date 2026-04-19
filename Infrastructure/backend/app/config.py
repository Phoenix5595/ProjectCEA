"""Configuration loader for the backend service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.config import YamlConfigLoader


class ConfigLoader(YamlConfigLoader):
    """Loads and parses the backend's ``config.yaml``."""

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            config_path,
            search_paths=[
                Path(__file__).parent.parent.parent.parent / "config.yaml",
                Path(__file__).parent.parent.parent / "config.yaml",
            ],
        )

    def get_locations(self) -> list[str]:
        """Get list of available locations."""
        sensors = self._config.get("sensors", {})
        return sensors.get("locations", [])

    def get_sensors_for_location(self, location: str) -> dict[str, Any]:
        """Get sensor configuration for a specific location."""
        sensors = self._config.get("sensors", {})
        location_map = {
            "Flower Room": "flower_room",
            "Veg Room": "veg_room",
            "Lab": "lab",
            "Outside": "outside",
        }
        config_key = location_map.get(location, location.lower().replace(" ", "_"))
        location_config = sensors.get(config_key, {})
        return location_config.get("clusters", {})
