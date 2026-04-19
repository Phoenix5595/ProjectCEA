"""Configuration loader for 1-Wire reader."""

from __future__ import annotations

from pathlib import Path

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

    def get_devices(self) -> dict[str, str]:
        """Return mapping device_id -> sensor_name (e.g. 28-xxxx -> lab_temp)."""
        return dict(self._config.get("devices", {}))

    def get_polling_interval_seconds(self) -> float:
        return float(self._config.get("polling", {}).get("interval_seconds", 1))
