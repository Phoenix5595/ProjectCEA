"""Configuration loader for 1-Wire reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.config import YamlConfigLoader


class ConfigLoader(YamlConfigLoader):
    """Loads ``onewire_config.yaml``: device id -> sensor name mapping."""

    def __init__(self, config_path: str | None = None) -> None:
        base = Path(__file__).parent.parent
        super().__init__(
            config_path,
            search_paths=[
                base / "onewire_config.yaml",
                Path("/etc/cea/onewire_config.yaml"),
            ],
        )
        self._service_settings: dict[str, Any] = {}

    def get_devices(self) -> dict[str, str]:
        """Return mapping device_id -> sensor_name (e.g. 28-xxxx -> lab_temp)."""
        return dict(self._config.get("devices", {}))

    def get_polling_interval_seconds(self) -> float:
        """Return the effective DB/cache setting or YAML fallback interval."""
        return float(
            self._service_settings.get(
                "poll_interval_seconds",
                self._config.get("polling", {}).get("interval_seconds", 1),
            )
        )

    def get_service_settings_fallback(self) -> dict[str, int]:
        """Return the YAML polling interval used when DB/cache lacks a value."""
        return {"poll_interval_seconds": self._config.get("polling", {}).get("interval_seconds", 1)}

    def apply_service_settings(self, values: dict[str, Any]) -> None:
        """Apply a resolved service settings snapshot to the polling interval."""
        if "poll_interval_seconds" in values:
            self._service_settings["poll_interval_seconds"] = values["poll_interval_seconds"]
