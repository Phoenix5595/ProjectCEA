"""Config repository — YAML configuration reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.repositories.base import BaseRepository

if TYPE_CHECKING:
    from asyncpg import Pool

import yaml


class ConfigRepository(BaseRepository):
    """Repository for configuration data.

    Wraps YAML config reads so routes don't open files directly.
    Does not require a database pool (config is file-based).
    """

    def __init__(self, config_path: str | Path, pool: Pool | None = None) -> None:
        super().__init__(pool)
        self._config_path = Path(config_path)

    def get_full_config(self) -> dict[str, Any]:
        with open(self._config_path) as f:
            return yaml.safe_load(f)

    def get_locations(self) -> list[str]:
        config = self.get_full_config()
        sensors = config.get("sensors", {})
        return sensors.get("locations", [])
