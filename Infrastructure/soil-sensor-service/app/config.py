"""Configuration loader for the soil sensor service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.config import YamlConfigLoader


class ConfigLoader(YamlConfigLoader):
    """Loads and parses ``soil_sensor_config.yaml``.

    Serial + polling settings only: sensor assignment authority lives in
    the `sensor_registry` table, so no ``sensors:`` list exists here.
    """

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            config_path,
            search_paths=[
                Path(__file__).parent.parent / "soil_sensor_config.yaml",
            ],
        )
        self._service_settings: dict[str, Any] = {}

    def get_rs485_config(self) -> dict[str, Any]:
        """Get RS485 serial port configuration."""
        return self._config.get("rs485", {"port": "/dev/serial0", "baudrate": 9600, "timeout": 1.0})

    def get_polling_config(self) -> dict[str, Any]:
        """Return YAML polling defaults overridden by loaded service settings."""
        polling = dict(self._config.get("polling", {}))
        polling.setdefault("interval_seconds", 5)
        polling.setdefault("discovery_interval_seconds", 30)
        if "poll_interval_seconds" in self._service_settings:
            polling["interval_seconds"] = self._service_settings["poll_interval_seconds"]
        if "discovery_interval_seconds" in self._service_settings:
            polling["discovery_interval_seconds"] = self._service_settings[
                "discovery_interval_seconds"
            ]
        return polling

    def get_service_settings_fallback(self) -> dict[str, Any]:
        """Return the YAML values used when DB/cache settings are absent."""
        polling = self._config.get("polling", {})
        return {
            "poll_interval_seconds": polling.get("interval_seconds", 5),
            "discovery_interval_seconds": polling.get("discovery_interval_seconds", 30),
        }

    def apply_service_settings(self, values: dict[str, Any]) -> None:
        """Apply a resolved service settings snapshot to effective intervals."""
        for key in ("poll_interval_seconds", "discovery_interval_seconds"):
            if key in values:
                self._service_settings[key] = values[key]
