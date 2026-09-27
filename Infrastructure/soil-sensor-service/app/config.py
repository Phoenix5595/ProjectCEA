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
                Path(
                    "/home/antoine/ProjectCEA/Infrastructure/soil-sensor-service/"
                    "soil_sensor_config.yaml"
                ),
            ],
        )

    def get_rs485_config(self) -> dict[str, Any]:
        """Get RS485 serial port configuration."""
        return self._config.get("rs485", {"port": "/dev/serial0", "baudrate": 9600, "timeout": 1.0})

    def get_polling_config(self) -> dict[str, Any]:
        """Get polling interval configuration."""
        return self._config.get("polling", {"interval_seconds": 5})
