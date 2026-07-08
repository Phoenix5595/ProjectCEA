"""Configuration loader for YAML config files.

This is NOT a subclass of ``shared.config.YamlConfigLoader`` — that is
intentional. The shared loader provides mechanical YAML loading with
search-path resolution; this loader adds Pydantic schema validation
(``ConfigSchema``), device-type canonicalization (``heater → heating``,
etc.), and Flower Room legacy config merging. Forcing it into the shared
hierarchy would pollute other services. See ``shared/config.py:20-29``
for the full rationale.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any

import yaml

from app.models.device_registry import DeviceUpdate
from app.repositories.devices import DeviceRepository

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Canonical ``device_type`` vocabulary used by the control code
# (``device_processor``, ``device_controller``, ``pid_controller_manager``,
# ``vpd_cascade_controller``). Every other code path should eventually use
# these names only.
_CANONICAL_DEVICE_TYPES: frozenset[str] = frozenset(
    {
        "heating",
        "cooling",
        "humidifier",
        "dehumidifier",
        "co2",
        "exhaust",
        "light",
    }
)

# Safe, unambiguous YAML aliases rewritten in-memory to their canonical form on
# load. ONLY include mappings where every code path agrees on the semantics:
# - ``heater`` → ``heating``: safety-limits path (config.py) currently checks
#   ``"heater"``; PID / VPD / device_controller all check ``"heating"``.
#   Aliasing ``heater → heating`` AND updating the safety-limits branch below
#   to check ``"heating"`` converges both paths with zero behaviour change for
#   the single canonical spelling.
#
# Explicitly NOT aliased:
# - ``fan``: ambiguous. ``config.py`` maps it to a cooling PID setpoint,
#   ``device_controller`` has no handler, and operator intent is unclear
#   (exhaust? circulation? cooling fan?). Left as-is and surfaced by the
#   startup validator below so the operator sees a WARNING next time a fan
#   device is enabled. Decide intent, THEN alias.
_DEVICE_TYPE_ALIASES: dict[str, str] = {
    "heater": "heating",
}


def _canonicalize_device_types(config: dict[str, Any]) -> None:
    """Rewrite YAML ``device_type`` aliases to canonical names in-place.

    Walks ``devices.<room>.<cluster>.<device_name>.device_type`` and:
    - replaces known aliases with their canonical form (INFO log once per
      alias), and
    - emits a WARNING for any ``device_type`` that is neither canonical nor a
      known alias, so drift is visible at startup.

    Does NOT touch YAML on disk. Operator-facing config stays whatever the
    operator wrote; canonicalization is a runtime concern.
    """
    devices = config.get("devices")
    if not isinstance(devices, dict):
        return

    aliases_applied: dict[str, int] = {}
    unknown_types: dict[str, list[str]] = {}

    for room_name, room in devices.items():
        if not isinstance(room, dict):
            continue
        for cluster_name, cluster in room.items():
            if not isinstance(cluster, dict):
                continue
            for device_name, dev_info in cluster.items():
                if not isinstance(dev_info, dict):
                    continue
                raw = dev_info.get("device_type")
                if not isinstance(raw, str):
                    continue
                if raw in _DEVICE_TYPE_ALIASES:
                    canonical = _DEVICE_TYPE_ALIASES[raw]
                    dev_info["device_type"] = canonical
                    aliases_applied[raw] = aliases_applied.get(raw, 0) + 1
                elif raw not in _CANONICAL_DEVICE_TYPES:
                    unknown_types.setdefault(raw, []).append(
                        f"{room_name}/{cluster_name}/{device_name}"
                    )

    for alias, count in aliases_applied.items():
        logger.info(
            "device_type alias '%s' -> '%s' applied to %d device(s) at YAML load",
            alias,
            _DEVICE_TYPE_ALIASES[alias],
            count,
        )
    for unknown, paths in unknown_types.items():
        preview = ", ".join(paths[:5])
        extra = f" (+{len(paths) - 5} more)" if len(paths) > 5 else ""
        logger.warning(
            "device_type '%s' is not in the canonical set %s and has no known "
            "alias. %d device(s) affected: %s%s. Control-code behaviour for "
            "this type may be undefined; consider renaming to a canonical name.",
            unknown,
            sorted(_CANONICAL_DEVICE_TYPES),
            len(paths),
            preview,
            extra,
        )


def _merge_flower_room_devices_into_main(config: dict[str, Any]) -> bool:
    """Move Flower Room equipment from legacy ``front``/``back`` under ``devices:`` into ``main``.

    The schema requires all Flower actuators under ``main`` only. Channel saves from the UI
    sometimes still wrote ``front``/``back``; merging fixes validation and control APIs that
    read ``Flower Room``/``main``.

    Returns:
        True if ``config`` was modified.
    """
    devices = config.get("devices")
    if not isinstance(devices, dict):
        return False
    fr = devices.get("Flower Room")
    if not isinstance(fr, dict):
        return False

    changed = False
    main = fr.get("main")
    if not isinstance(main, dict):
        if main is not None:
            logger.warning("Flower Room 'main' is not a dict; replacing with an empty dict")
        main = {}
        fr["main"] = main
        changed = True

    for legacy in ("front", "back"):
        leg = fr.get(legacy)
        if not isinstance(leg, dict) or len(leg) == 0:
            continue
        changed = True
        for device_name, dev_info in leg.items():
            if device_name in main:
                logger.warning(
                    "Flower Room: skipping merge of device %r from %r — already defined under main",
                    device_name,
                    legacy,
                )
                continue
            main[device_name] = dev_info
        del fr[legacy]

    for legacy in ("front", "back"):
        if legacy in fr and isinstance(fr[legacy], dict) and len(fr[legacy]) == 0:
            del fr[legacy]
            changed = True

    return changed


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
        from app.control.engine_config_cache import EngineConfigCache

        self._device_cache = EngineConfigCache(ttl_seconds=30.0)
        self._yaml_has_devices = False
        self._bootstrap_checked = False
        self.load()

    def set_device_repo(self, device_repo: DeviceRepository) -> None:
        """Set the DeviceRepository for DB-backed device queries."""
        self._device_repo = device_repo

    def load(self) -> None:
        """Load configuration from YAML files."""
        # Load main config
        with open(self.config_path) as f:
            self._config = yaml.safe_load(f) or {}
        if _merge_flower_room_devices_into_main(self._config):
            try:
                with open(self.config_path, "w") as f:
                    yaml.dump(
                        self._config,
                        f,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
                logger.info(
                    "Merged Flower Room device entries from legacy clusters into main; saved %s",
                    self.config_path,
                )
            except OSError as e:
                logger.error("Failed to persist Flower Room device merge: %s", e)
                raise
        # M1: _canonicalize_device_types is kept for backward compatibility but
        # no longer called on every load (migration 009 already canonicalized
        # devices in the DB).  YAML on disk stays as-is.
        self._yaml_has_devices = bool(self._config.get("devices"))
        self._validate_config()

        # Load schedules if exists
        if self.schedules_path.exists():
            with open(self.schedules_path) as f:
                schedules_data = yaml.safe_load(f) or {}
                self._schedules = schedules_data.get("schedules", [])
        else:
            # Check if schedules are in main config
            if "schedules" in self._config:
                self._schedules = self._config["schedules"]

        # Load rules if exists
        if self.rules_path.exists():
            with open(self.rules_path) as f:
                rules_data = yaml.safe_load(f) or {}
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
        """Get device configuration from DB (cached with 30s TTL).

        Falls back to YAML devices block when no DeviceRepository is set
        (early initialization before database is ready).
        """
        if self._device_repo is None:
            return self._config.get("devices", {})

        if not self._bootstrap_checked:
            self._bootstrap_checked = True
            db_devices = await self._device_repo.get_all_as_hierarchy()
            if not db_devices and self._yaml_has_devices:
                logger.error(
                    "device_registry is empty but automation_config.yaml has devices. "
                    "Run: cd Infrastructure/automation-service && alembic upgrade head"
                )

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

        # Use defaults if not configured. ``heater`` is aliased to ``heating``
        # at YAML load by ``_canonicalize_device_types``. The ``fan`` branch is
        # preserved as-is (not in the alias list — semantics ambiguous, see
        # _DEVICE_TYPE_ALIASES comment) to avoid changing behaviour for any
        # fan device until the operator confirms intent.
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

    async def update_device_config(
        self,
        location: str,
        cluster: str,
        device_name: str,
        display_name: str | None = None,
        device_type: str | None = None,
    ) -> bool:
        """Update device configuration (display_name, device_type) in DB.

        Light devices are updated via DeviceRepository.update_light().
        Non-light devices are updated via DeviceRepository.update_device().

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            display_name: Optional display name to set
            device_type: Optional device type to set

        Returns:
            True if successful, False otherwise
        """
        if self._device_repo is None:
            logger.warning(
                "DeviceRepository not set, falling back to YAML for update_device_config"
            )
            return self._update_device_config_yaml(
                location, cluster, device_name, display_name, device_type
            )

        try:
            device_id = await self._device_repo.get_device_id(location, cluster, device_name)
            if device_id is None:
                logger.error(f"Device {device_name} not found in DB at {location}/{cluster}")
                return False

            current_type = await self._device_repo.get_device_type_by_id(device_id)
            if current_type is None:
                logger.error(f"Could not determine device_type for {device_name}")
                return False

            if current_type == "light":
                fields: dict[str, Any] = {}
                if display_name is not None:
                    fields["display_name"] = display_name
                if device_type is not None:
                    fields["device_type"] = device_type

                if not fields:
                    return True

                result = await self._device_repo.update_light(device_id, **fields)
            else:
                update_fields: dict[str, Any] = {}
                if display_name is not None:
                    update_fields["display_name"] = display_name
                if device_type is not None:
                    logger.warning(
                        "device_type update not supported for non-light devices via update_device_config"
                    )

                if not update_fields:
                    return True

                result = await self._device_repo.update_device(
                    device_id, DeviceUpdate(**update_fields)
                )

            if result is not None:
                logger.info(f"Updated device config in DB: {location}/{cluster}/{device_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating device config: {e}")
            return False

    def _update_device_config_yaml(
        self,
        location: str,
        cluster: str,
        device_name: str,
        display_name: str | None = None,
        device_type: str | None = None,
    ) -> bool:
        """Legacy YAML fallback for update_device_config (deprecated)."""
        with self._config_lock:
            try:
                if "devices" not in self._config:
                    self._config["devices"] = {}
                if location not in self._config["devices"]:
                    self._config["devices"][location] = {}
                if cluster not in self._config["devices"][location]:
                    self._config["devices"][location][cluster] = {}
                if device_name not in self._config["devices"][location][cluster]:
                    raise ValueError(f"Device {device_name} not found in {location}/{cluster}")

                device_config = self._config["devices"][location][cluster][device_name]
                if display_name is not None:
                    device_config["display_name"] = display_name
                if device_type is not None:
                    device_config["device_type"] = device_type

                self.write_full_config(self._config)
                logger.info(
                    f"Updated device config (YAML fallback): {location}/{cluster}/{device_name}"
                )
                return True
            except Exception as e:
                logger.error(f"Error updating device config (YAML fallback): {e}")
                return False

    async def update_light_dimming_assignment(
        self,
        location: str,
        cluster: str,
        device_name: str,
        *,
        board_id: int | None,
        dimming_channel: int | None,
    ) -> bool:
        """Update a light device's DFR0971 dimming mapping in DB.

        This only updates configuration fields; hardware commands are handled by the control loop.
        """
        if self._device_repo is None:
            logger.warning(
                "DeviceRepository not set, falling back to YAML for update_light_dimming_assignment"
            )
            return self._update_light_dimming_assignment_yaml(
                location, cluster, device_name, board_id=board_id, dimming_channel=dimming_channel
            )

        try:
            device_id = await self._device_repo.get_device_id(location, cluster, device_name)
            if device_id is None:
                logger.error(f"Device {device_name} not found in DB at {location}/{cluster}")
                return False

            fields: dict[str, Any] = {}
            if board_id is None or dimming_channel is None:
                fields["dimming_board_id"] = None
                fields["dimming_channel"] = None
            else:
                fields["dimming_board_id"] = int(board_id)
                fields["dimming_channel"] = int(dimming_channel)

            result = await self._device_repo.update_light(device_id, **fields)
            if result is not None:
                logger.info(
                    "Updated light dimming assignment in DB: %s/%s/%s board_id=%s channel=%s",
                    location,
                    cluster,
                    device_name,
                    board_id,
                    dimming_channel,
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating light dimming assignment: {e}")
            return False

    def _update_light_dimming_assignment_yaml(
        self,
        location: str,
        cluster: str,
        device_name: str,
        *,
        board_id: int | None,
        dimming_channel: int | None,
    ) -> bool:
        """Legacy YAML fallback for update_light_dimming_assignment (deprecated)."""
        with self._config_lock:
            try:
                devices_root = self._config.get("devices", {})
                if location not in devices_root or cluster not in devices_root.get(location, {}):
                    raise ValueError(f"Unknown location/cluster: {location}/{cluster}")
                devs = devices_root[location][cluster]
                if device_name not in devs:
                    raise ValueError(f"Device {device_name} not found in {location}/{cluster}")

                device_config = devs[device_name]
                if not isinstance(device_config, dict):
                    raise ValueError(
                        f"Invalid device config shape for {location}/{cluster}/{device_name}"
                    )

                if board_id is None or dimming_channel is None:
                    device_config.pop("dimming_board_id", None)
                    device_config.pop("dimming_channel", None)
                else:
                    device_config["dimming_board_id"] = int(board_id)
                    device_config["dimming_channel"] = int(dimming_channel)

                self.write_full_config(self._config)
                logger.info(
                    "Updated light dimming assignment (YAML fallback): %s/%s/%s board_id=%s channel=%s",
                    location,
                    cluster,
                    device_name,
                    board_id,
                    dimming_channel,
                )
                return True
            except Exception as e:
                logger.error(f"Error updating light dimming assignment (YAML fallback): {e}")
                return False
