"""Base class for service YAML config loaders.

Lifted out of four byte-similar implementations:

  - ``onewire-worker-service/app/config.py``
  - ``weather-service/app/config.py``
  - ``soil-sensor-service/app/config.py``
  - ``backend/app/config.py``

Each service still owns a thin ``ConfigLoader`` subclass that supplies
its candidate search paths and exposes typed accessor helpers
(``get_weather_config()``, ``get_sensors()``, etc.). The shared base
provides only the mechanics that were duplicated four times:

  1. Search-path resolution with a ``FileNotFoundError`` if nothing matches.
  2. ``yaml.safe_load`` of the chosen path with empty-file tolerance.
  3. ``get("a.b.c", default)`` dot-notation lookup.
  4. ``reload()`` for SIGHUP-style refreshes (currently unused but cheap).

Deliberately *not* in scope here:

- The ``automation-service`` config loader (520 lines, Pydantic validation,
  device-type alias canonicalization). Its shape is genuinely different and
  pulling it into the same hierarchy would force every other service to
  carry weight it doesn't need.
- Environment-variable overrides (e.g. injecting ``POSTGRES_PASSWORD`` from
  the env at ``get_database_config`` time). Those are *behavioral* choices
  per service and remain owned by the subclass — the base class only loads
  YAML, it doesn't decide which keys deserve env-var precedence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class YamlConfigLoader:
    """Mechanical YAML loader with search-path resolution and dotted lookup.

    Subclasses pass a list of candidate ``Path`` objects via ``search_paths``;
    the first existing one wins. Pass an explicit ``config_path`` to bypass
    the search entirely (useful for tests).

    The loaded document is exposed read-only as ``self._config`` (subclasses
    use it to implement their typed accessor methods). Mutating it after load
    would silently desync from disk on a future reload, so don't.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        search_paths: list[Path] | None = None,
    ) -> None:
        if config_path is None:
            for candidate in search_paths or []:
                if candidate.exists():
                    config_path = candidate
                    break
        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(
                f"Config file not found. Tried explicit={config_path!r} "
                f"and search_paths={[str(p) for p in (search_paths or [])]}"
            )
        self.config_path: Path = Path(config_path)
        self._config: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """(Re)load the YAML document from ``self.config_path``."""
        with open(self.config_path) as f:
            self._config = yaml.safe_load(f) or {}
        logger.info(f"Loaded config from {self.config_path}")

    # Alias to make the SIGHUP-on-reload pattern self-documenting at call sites.
    reload = load

    def get(self, key: str, default: Any = None) -> Any:
        """Dotted-path lookup (e.g. ``"weather.station_icao"``).

        Returns ``default`` for any missing intermediate key or non-dict
        intermediate value. Does not raise.
        """
        value: Any = self._config
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return default
            else:
                return default
        return value


__all__ = ["YamlConfigLoader"]
