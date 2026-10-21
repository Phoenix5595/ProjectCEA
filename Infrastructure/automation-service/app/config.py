"""Configuration loader for YAML config files.

This is NOT a subclass of ``shared.config.YamlConfigLoader`` — that is
intentional. The shared loader provides mechanical YAML loading with
search-path resolution; this loader adds Pydantic schema validation and
separate schedule loading. Device identity and assignments belong to the
runtime registry snapshot, not this YAML file.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any

import yaml

from app.repositories.devices import DeviceRepository
from shared.service_settings import ServiceSettings

if TYPE_CHECKING:
    from app.control.runtime_device_registry import RuntimeDeviceRegistry

logger = logging.getLogger(__name__)


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict.

    Returns an empty dict if the file does not exist or is empty.
    """
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


class ConfigLoader:
    """Loads YAML configuration and overlays live service settings."""

    def __init__(
        self,
        config_path: str | None = None,
        *,
        settings_cache_path: str | Path | None = None,
    ):
        """Initialize config loader.

        Args:
            config_path: Path to automation_config.yaml. If None, searches in common locations.
            settings_cache_path: Optional settings-cache path override for focused tests.
        """
        if config_path is None:
            possible_paths = [Path(__file__).parent.parent / "automation_config.yaml"]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break

        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self._config_lock = threading.Lock()
        self._device_repo: DeviceRepository | None = None
        self._runtime_device_registry: RuntimeDeviceRegistry | None = None
        self._settings_cache_path = settings_cache_path
        self._settings_pool: Any | None = None
        self._service_settings_loaded = False
        self._settings_fallback_values: dict[str, Any] = {}
        self._effective_settings: dict[str, Any] = {}
        from app.control.engine_config_cache import EngineConfigCache

        self._device_cache = EngineConfigCache(ttl_seconds=30.0)
        self.load()

    def set_device_repo(self, device_repo: DeviceRepository) -> None:
        """Set the DeviceRepository for DB-backed device queries."""
        self._device_repo = device_repo

    def set_runtime_device_registry(self, registry: RuntimeDeviceRegistry) -> None:
        """Use the installed immutable runtime snapshot for device reads."""
        self._runtime_device_registry = registry

    def load(self) -> None:
        """Load YAML configuration and preserve the effective service settings."""
        self._config = _load_yaml_file(self.config_path)
        control = self._config.get("control")
        if isinstance(control, dict):
            control.pop("default_setpoints", None)
        self._validate_config()

        self._settings_fallback_values = self._settings_from_yaml()
        if not self._service_settings_loaded:
            self._effective_settings = dict(self._settings_fallback_values)

        logger.info(f"Loaded config from {self.config_path}")

    async def load_service_settings(self, pool: Any) -> None:
        """Hydrate automation settings from DB, cache, then YAML fallback."""
        self._settings_pool = pool
        settings = await ServiceSettings.load(
            pool,
            "automation",
            cache_path=self._settings_cache_path,
            fallback=self._settings_fallback_values,
        )
        self.apply_service_settings(settings)
        self._service_settings_loaded = True

    async def refresh_service_settings(self) -> None:
        """Reload effective settings through the shared DB/cache/YAML resolver."""
        if self._settings_pool is None:
            raise RuntimeError("Service settings pool is not configured")
        await self.load_service_settings(self._settings_pool)

    async def run_service_settings_refresh(self) -> None:
        """Poll for settings updates and atomically install changed values."""
        if self._settings_pool is None:
            raise RuntimeError("Service settings pool is not configured")
        await ServiceSettings.run_refresh(
            self._settings_pool,
            "automation",
            self.apply_service_settings,
            cache_path=self._settings_cache_path,
            fallback=self._settings_fallback_values,
        )

    def apply_service_settings(self, settings: dict[str, Any]) -> None:
        """Install one resolved settings snapshot for synchronous runtime reads."""
        effective = dict(self._settings_fallback_values)
        effective.update(settings)
        self._effective_settings = effective
        self._service_settings_loaded = True

    def service_settings_fallback(self) -> dict[str, Any]:
        """Return the YAML-derived fallback values for API cross-setting checks."""
        return dict(self._settings_fallback_values)

    def _settings_from_yaml(self) -> dict[str, Any]:
        control = self._config.get("control") or {}
        settings = {
            key: control[key]
            for key in (
                "update_interval",
                "last_good_hold_period",
                "binary_hysteresis",
                "pid_limits",
                "safety_limits",
            )
            if key in control
        }

        if "pid_defaults" in control:
            settings["pid_defaults"] = control["pid_defaults"]
        return settings

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration with live service settings via dot notation."""
        keys = key.split(".")
        if keys[0] == "control":
            value: Any = self.get_control_config()
            keys = keys[1:]
            if not keys:
                return value
        else:
            value = self._config
        for part in keys:
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return default
            else:
                return default
        return value

    def get_hardware_config(self) -> dict[str, Any]:
        """Get hardware configuration."""
        return self._config.get("hardware", {})

    async def get_devices(self) -> dict[str, Any]:
        """Get device configuration from the installed runtime snapshot."""
        if self._runtime_device_registry is not None:
            return dict(self._runtime_device_registry.snapshot.hierarchy)
        if self._device_repo is None:
            logger.error("get_devices() called before DeviceRepository is set")
            return {}

        return await self._device_cache.get_device_hierarchy(self._device_repo.get_all_as_hierarchy)

    def get_control_config(self) -> dict[str, Any]:
        """Get YAML control config overlaid with effective service settings."""
        control = dict(self._config.get("control", {}))
        control.pop("default_setpoints", None)
        control.update(self._effective_settings)
        return control

    def get_pid_defaults(self) -> dict[str, Any]:
        """Get current DB/cache/YAML PID defaults."""
        return self._effective_settings.get("pid_defaults", {})

    def get_safety_limits(self) -> dict[str, float]:
        """Get effective safety limits."""
        return self._effective_settings.get(
            "safety_limits", self._config.get("control", {}).get("safety_limits", {})
        )

    def get_sensor_mapping(self) -> dict[str, Any]:
        """Get sensor mapping."""
        return self._config.get("sensors", {})

    CONTROL_LOOP_INTERVAL_MAX = 5

    def get_update_interval(self) -> int:
        """Get current control loop interval in seconds (1–5, enforced)."""
        raw = self._effective_settings.get(
            "update_interval", self._config.get("control", {}).get("update_interval", 1)
        )
        try:
            val = int(raw) if raw is not None else 1
        except (TypeError, ValueError):
            val = 1
        return max(1, min(self.CONTROL_LOOP_INTERVAL_MAX, val))

    def get_last_good_hold_period(self) -> int:
        """Get the effective last-good hold period."""
        raw = self._effective_settings.get(
            "last_good_hold_period",
            self._config.get("control", {}).get("last_good_hold_period", 30),
        )
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 30

    def get_binary_hysteresis(self) -> float:
        """Get the effective global binary hysteresis band."""
        raw = self._effective_settings.get(
            "binary_hysteresis", self._config.get("control", {}).get("binary_hysteresis", 0.1)
        )
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.1

    def get_interlocks(self) -> list[dict[str, Any]]:
        """Get interlock rules."""
        return self._config.get("interlocks", [])

    def get_pid_params_for_device(self, device_type: str) -> dict[str, float]:
        """Get effective PID gains for a device type."""
        settings_name = {
            "heating": "heater",
            "cooling": "fan",
        }.get(device_type, device_type)
        defaults = self.get_pid_defaults()
        gains = defaults.get(settings_name) or defaults.get("default") or {}
        return {
            "kp": gains.get("kp", 10.0),
            "ki": gains.get("ki", 0.01),
            "kd": gains.get("kd", 0.0),
        }

    async def refresh_runtime_device_snapshot(self) -> int:
        """Install one complete snapshot after a committed device-registry mutation."""
        if self._runtime_device_registry is None:
            raise RuntimeError("Runtime device registry is not configured")
        snapshot = await self._runtime_device_registry.reload_after_commit()
        return snapshot.version

    def reload(self) -> None:
        """Reload configuration from files (incremental reload)."""
        self.load()
        logger.info("Configuration reloaded")
        # Note: Incremental reload - changes applied as loaded, not atomic

    def _validate_config(self) -> None:
        """Validate loaded config against Pydantic schema.

        Raises:
            ValueError: If config validation fails.
        """
        from pydantic import ValidationError

        from app.models.config_schema import AppConfig

        try:
            AppConfig.model_validate(self._config)
            logger.info("Config validation passed")
        except ValidationError as e:
            errors = []
            for error in e.errors():
                field_path = " -> ".join(str(loc) for loc in error["loc"])
                errors.append(f"  {field_path}: {error['msg']}")
            error_msg = "Config validation failed:\n" + "\n".join(errors)
            logger.error(error_msg)
            raise ValueError(error_msg) from e

    def validate_in_memory(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Validate a candidate config dict in-memory using AppConfig.

        Args:
            candidate: Full config dict to validate.

        Returns:
            The candidate dict (passthrough on success).

        Raises:
            ValueError: If validation fails.
        """
        from pydantic import ValidationError

        from app.models.config_schema import AppConfig

        try:
            AppConfig.model_validate(candidate)
        except ValidationError as e:
            errors = []
            for error in e.errors():
                field_path = " -> ".join(str(loc) for loc in error["loc"])
                errors.append(f"  {field_path}: {error['msg']}")
            error_msg = "In-memory config validation failed:\n" + "\n".join(errors)
            logger.error(error_msg)
            raise ValueError(error_msg) from e
        return candidate

    def write_full_config(self, merged_raw: dict[str, Any]) -> None:
        """Atomically write the full config dict to disk (temp file + os.replace).

        The caller is responsible for holding self._config_lock for the duration
        of the write.  This method validates the candidate in-memory before
        writing, then performs an atomic replace.

        Devices live in the database now; the ``devices:`` section is excluded
        from YAML writes so that config mutations do not clobber the DB source
        of truth.

        Args:
            merged_raw: The complete merged config dict to persist.

        Raises:
            ValueError: If in-memory validation fails.
            OSError: If the atomic write fails.
        """
        merged_raw = dict(merged_raw)
        merged_raw.pop("devices", None)
        self.validate_in_memory(merged_raw)

        dir_path = self.config_path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as f:
                yaml.dump(
                    merged_raw,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
            os.replace(tmp_path, self.config_path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
