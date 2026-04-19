"""Configuration loader for the backend service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.config import YamlConfigLoader


class ConfigLoader(YamlConfigLoader):
    """Loads and parses the backend's ``config.yaml``.

    Search order (first hit wins):
      1. ``Infrastructure/backend/config.yaml`` — the canonical, deployed
         location. Lives next to the service code so it ships in every
         release tarball under ``rsync Infrastructure/``.
      2. ``Infrastructure/config.yaml`` — legacy path. Kept so that an
         operator who manually drops a config there during ad-hoc debugging
         still gets it picked up.
      3. ``<repo-root>/config.yaml`` — pre-Phase 6 layout, where the file
         lived at the top level of the source repo. Kept so that running
         the backend out of a source checkout (not a release symlink) still
         resolves the file.

    Background: pre-Phase 6, only path 3 was populated, but path 3 is
    *outside* what ``deploy.sh`` rsyncs into a release dir, so the deployed
    backend's ConfigLoader silently couldn't find the file (the resulting
    500 on ``/api/config/locations`` was masked by the frontend not
    consuming that endpoint). Phase 6 surfaced the bug via a more verbose
    ``FileNotFoundError`` and fixed the layout.
    """

    def __init__(self, config_path: str | None = None) -> None:
        super().__init__(
            config_path,
            search_paths=[
                Path(__file__).parent.parent / "config.yaml",
                Path(__file__).parent.parent.parent / "config.yaml",
                Path(__file__).parent.parent.parent.parent / "config.yaml",
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
