"""Configuration loader for 1-Wire reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shared.logging import get_logger

logger = get_logger(__name__)


class ConfigLoader:
    """Loads onewire_config.yaml: device id -> sensor name mapping."""

    def __init__(self, config_path: str | None = None) -> None:
        if config_path is None:
            base = Path(__file__).parent.parent
            possible = [base / "onewire_config.yaml", Path("/etc/cea/onewire_config.yaml")]
            for p in possible:
                if p.exists():
                    config_path = str(p)
                    break
        if not config_path or not Path(config_path).exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        with open(self.config_path) as f:
            self._config = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {self.config_path}")

    def get_devices(self) -> dict[str, str]:
        """Return mapping device_id -> sensor_name (e.g. 28-xxxx -> lab_temp)."""
        return dict(self._config.get("devices", {}))

    def get_polling_interval_seconds(self) -> float:
        return float(self._config.get("polling", {}).get("interval_seconds", 1))
