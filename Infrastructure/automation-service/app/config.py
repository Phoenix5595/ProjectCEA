"""Configuration loader for YAML config files.

This is NOT a subclass of ``shared.config.YamlConfigLoader`` — that is
intentional. The shared loader provides mechanical YAML loading with
search-path resolution; this loader adds Pydantic schema validation and
separate schedule/rule loading. Device identity and assignments belong to the
runtime registry snapshot, not this YAML file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any

import yaml

from app.repositories.devices import DeviceRepository

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
    """Loads and parses YAML configuration files."""

    def __init__(self, config_path: str | None = None):
        """Initialize config loader.

        Args:
            config_path: Path to automation_config.yaml. If None, searches in common locations.
        """
        if config_path is None:
            # Try common locations
            possible_paths = [
                Path(__file__).parent.parent / "automation_config.yaml",
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break

        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        self.config_path = Path(config_path)
        self.schedules_path = self.config_path.parent / "schedules.yaml"
        self.rules_path = self.config_path.parent / "rules.yaml"
        self._config: dict[str, Any] = {}
        self._schedules: list[dict[str, Any]] = []
        self._rules: list[dict[str, Any]] = []
        self._config_lock = threading.Lock()
        self._device_repo: DeviceRepository | None = None
        self._runtime_device_registry: RuntimeDeviceRegistry | None = None
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
        """Load configuration from YAML files."""
        # Load main config
        self._config = _load_yaml_file(self.config_path)
        self._validate_config()

        # Load schedules if exists
        schedules_data = _load_yaml_file(self.schedules_path)
        if schedules_data:
            self._schedules = schedules_data.get("schedules", [])
        else:
            # Check if schedules are in main config
            if "schedules" in self._config:
                self._schedules = self._config["schedules"]

        # Load rules if exists
        rules_data = _load_yaml_file(self.rules_path)
        if rules_data:
            self._rules = rules_data.get("rules", [])
        else:
            # Check if rules are in main config
            if "rules" in self._config:
                self._rules = self._config["rules"]

        logger.info(f"Loaded config from {self.config_path}")
        if self._schedules:
            logger.info(f"Loaded {len(self._schedules)} schedules")
        if self._rules:
            logger.info(f"Loaded {len(self._rules)} rules")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'hardware.i2c_bus')."""
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
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
        """Get control configuration."""
        return self._config.get("control", {})

    def get_pid_config(self) -> dict[str, Any]:
        """Get PID configuration."""
        return self._config.get("control", {}).get("pid", {})

    def get_safety_limits(self) -> dict[str, float]:
        """Get safety limits."""
        return self._config.get("control", {}).get("safety_limits", {})

    def get_default_setpoints(self) -> dict[str, Any]:
        """Get default setpoints."""
        return self._config.get("control", {}).get("default_setpoints", {})

    def get_sensor_mapping(self) -> dict[str, Any]:
        """Get sensor mapping."""
        return self._config.get("sensors", {})

    CONTROL_LOOP_INTERVAL_MAX = 5  # seconds, non-negotiable

    def get_update_interval(self) -> int:
        """Get control loop update interval in seconds (1–5, enforced)."""
        raw = self._config.get("control", {}).get("update_interval", 1)
        try:
            val = int(raw) if raw is not None else 1
        except (TypeError, ValueError):
            val = 1
        return max(1, min(self.CONTROL_LOOP_INTERVAL_MAX, val))

    def get_schedules(self) -> list[dict[str, Any]]:
        """Get schedules."""
        return self._schedules

    def get_rules(self) -> list[dict[str, Any]]:
        """Get rules."""
        return self._rules

    def get_interlocks(self) -> list[dict[str, Any]]:
        """Get interlock rules."""
        return self._config.get("interlocks", [])

    def get_pid_params_for_device(self, device_type: str) -> dict[str, float]:
        """Get PID parameters for a device type.

        Args:
            device_type: Device type (e.g., 'heater', 'co2')

        Returns:
            Dict with 'kp', 'ki', 'kd' values
        """
        pid_config = self.get_pid_config()

        # Try device-specific params first
        kp_key = f"{device_type}_kp"
        ki_key = f"{device_type}_ki"
        kd_key = f"{device_type}_kd"

        kp = pid_config.get(kp_key, pid_config.get("default_kp", 10.0))
        ki = pid_config.get(ki_key, pid_config.get("default_ki", 0.01))
        kd = pid_config.get(kd_key, pid_config.get("default_kd", 0.0))

        return {"kp": kp, "ki": ki, "kd": kd}

    async def get_pid_setpoints_for_device(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> list[tuple]:
        """Get PID setpoints for a device with priorities.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_type: Device type (e.g., 'heater', 'fan', 'co2')

        Returns:
            List of (setpoint_type, priority) tuples sorted by priority (ascending)
            Lower priority number = higher priority
        """
        devices = await self.get_devices()
        device_info = devices.get(location, {}).get(cluster, {}).get(device_name, {})

        # Check if pid_setpoints is explicitly configured
        pid_setpoints: dict[str, int] | None = None
        if isinstance(device_info, dict):
            pid_setpoints = device_info.get("pid_setpoints") or None
            if not pid_setpoints:
                pid_setpoints = None

        if pid_setpoints:
            # Validate that it's a dict with integer priorities
            if not isinstance(pid_setpoints, dict):
                logger.warning(
                    f"Invalid pid_setpoints format for {location}/{cluster}/{device_name}, using defaults"
                )
                pid_setpoints = None
            else:
                # Validate priorities are positive integers
                for setpoint_type, priority in pid_setpoints.items():
                    if not isinstance(priority, int) or priority < 1:
                        logger.warning(
                            f"Invalid priority {priority} for {setpoint_type} in {location}/{cluster}/{device_name}, using defaults"
                        )
                        pid_setpoints = None
                        break

        # Device types are already canonicalized at the registry write boundary.
        if not pid_setpoints:
            if device_type == "heating":
                pid_setpoints = {"heating_setpoint": 1}
            elif device_type == "fan":
                pid_setpoints = {"cooling_setpoint": 1}
            elif device_type == "co2":
                pid_setpoints = {"co2": 1}
            else:
                # Unknown device type, return empty list
                logger.warning(
                    f"No default pid_setpoints for device_type '{device_type}', returning empty list"
                )
                return []

        # Sort by priority (ascending: lower number = higher priority)
        sorted_setpoints = sorted(pid_setpoints.items(), key=lambda x: x[1])
        return sorted_setpoints

    def invalidate_device_cache(self) -> None:
        """Invalidate the in-memory device hierarchy cache."""
        self._device_cache._device_hierarchy_cache = None
        self._device_cache._cache_timestamp = None
        logger.debug("Invalidated device hierarchy cache")

    async def refresh_runtime_device_snapshot(self) -> int:
        """Install one complete snapshot after a committed device-registry mutation."""
        if self._runtime_device_registry is None:
            raise RuntimeError("Runtime device registry is not configured")
        snapshot = await self._runtime_device_registry.reload_after_commit()
        return snapshot.version

    def reload(self) -> None:
        """Reload configuration from files (incremental reload)."""
        self._config.copy()
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
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
