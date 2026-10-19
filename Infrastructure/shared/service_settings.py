"""Database-backed service settings with durable last-known-good fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import inspect
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from shared.pid_limits import PidLimits

logger = logging.getLogger(__name__)

SETTINGS_CACHE_DIR = Path("/var/lib/projectcea/settings-cache")


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """Validation and API metadata for one persisted setting."""

    value_type: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    pattern: str | None = None
    read_only: bool = False


SETTINGS_SCHEMA: dict[str, dict[str, SettingSpec]] = {
    "automation": {
        "update_interval": SettingSpec("integer", minimum=1, maximum=5),
        "last_good_hold_period": SettingSpec("integer", minimum=0, maximum=3600),
        "binary_hysteresis": SettingSpec("number", minimum=0, maximum=10),
        "pid_limits": SettingSpec("object"),
        "pid_defaults": SettingSpec("object"),
        "safety_limits": SettingSpec("object"),
    },
    "soil": {
        "poll_interval_seconds": SettingSpec("integer", minimum=1, maximum=300),
        "discovery_interval_seconds": SettingSpec("integer", minimum=5, maximum=3600),
    },
    "onewire": {
        "poll_interval_seconds": SettingSpec("integer", minimum=1, maximum=300),
    },
    "weather": {
        "station_icao": SettingSpec("string", pattern=r"^[A-Z0-9]{4}$"),
        "poll_interval_seconds": SettingSpec("integer", minimum=60, maximum=86400),
        "api_url": SettingSpec("string"),
        "room_name": SettingSpec("string", read_only=True),
        "device_name": SettingSpec("string", read_only=True),
    },
}


class SettingValidationError(ValueError):
    """A setting value was rejected, with fields suitable for API errors."""

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        self.code = "setting_read_only" if reason == "setting_read_only" else "setting_invalid"
        super().__init__(reason)


class UnknownServiceError(ValueError):
    """The requested service has no registered settings."""

    code = "unknown_service"

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(f"Unknown service: {service}")


def _require_service(service: str) -> dict[str, SettingSpec]:
    try:
        return SETTINGS_SCHEMA[service]
    except KeyError as exc:
        raise UnknownServiceError(service) from exc


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_basic(key: str, value: Any, spec: SettingSpec) -> None:
    if spec.read_only:
        raise SettingValidationError(key, "setting_read_only")

    if spec.value_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif spec.value_type == "number":
        valid_type = _is_number(value)
    elif spec.value_type == "string":
        valid_type = isinstance(value, str)
    elif spec.value_type == "object":
        valid_type = isinstance(value, dict)
    else:  # pragma: no cover - registry entries are defined in this module
        raise ValueError(f"Unsupported setting type: {spec.value_type}")

    if not valid_type:
        raise SettingValidationError(key, f"must be a {spec.value_type}")

    if spec.value_type in ("integer", "number"):
        if spec.minimum is not None and value < spec.minimum:
            raise SettingValidationError(key, f"must be at least {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise SettingValidationError(key, f"must be at most {spec.maximum}")

    if spec.pattern is not None and re.fullmatch(spec.pattern, value) is None:
        raise SettingValidationError(key, f"must match {spec.pattern}")

    if key == "api_url" and not value.startswith("https://"):
        raise SettingValidationError(key, "must start with https://")


def _validate_pid_limits(value: dict[str, Any]) -> None:
    try:
        PidLimits.model_validate(value)
    except ValueError as exc:
        raise SettingValidationError("pid_limits", str(exc)) from exc


def _validate_pid_defaults(defaults: dict[str, Any], limits: Any) -> None:
    if not isinstance(limits, dict):
        limits = {}

    for device_type, gains in defaults.items():
        if not isinstance(gains, dict):
            raise SettingValidationError("pid_defaults", f"{device_type} must be an object")

        device_limits = limits.get(device_type)
        if device_limits is None:
            device_limits = {}
        if not isinstance(device_limits, dict):
            raise SettingValidationError("pid_limits", f"{device_type} must be an object")

        for parameter in ("kp", "ki", "kd"):
            if parameter not in gains:
                raise SettingValidationError(
                    "pid_defaults", f"{device_type}.{parameter} is required"
                )
            gain = gains[parameter]
            if not _is_number(gain):
                raise SettingValidationError(
                    "pid_defaults", f"{device_type}.{parameter} must be a number"
                )

            minimum = device_limits.get(f"{parameter}_min")
            maximum = device_limits.get(f"{parameter}_max")
            if minimum is not None and _is_number(minimum) and gain < minimum:
                raise SettingValidationError(
                    "pid_defaults", f"{device_type}.{parameter} must be at least {minimum}"
                )
            if maximum is not None and _is_number(maximum) and gain > maximum:
                raise SettingValidationError(
                    "pid_defaults", f"{device_type}.{parameter} must be at most {maximum}"
                )


def _validate_safety_limits(value: dict[str, Any]) -> None:
    for low_name, high_name in (
        ("min_temperature", "max_temperature"),
        ("min_humidity", "max_humidity"),
        ("min_co2", "max_co2"),
    ):
        if low_name not in value or high_name not in value:
            raise SettingValidationError(
                "safety_limits", f"{low_name} and {high_name} are required"
            )
        low, high = value[low_name], value[high_name]
        if not _is_number(low) or not _is_number(high):
            raise SettingValidationError(
                "safety_limits", f"{low_name} and {high_name} must be numbers"
            )
        if low >= high:
            raise SettingValidationError(
                "safety_limits", f"{low_name} must be less than {high_name}"
            )


def validate_settings(
    service: str,
    updates: Mapping[str, Any],
    *,
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a settings patch and return a plain copy of the patch.

    ``current`` supplies the effective values needed for cross-setting checks,
    such as checking new PID defaults against existing PID limits.
    """
    schema = _require_service(service)
    if not isinstance(updates, Mapping):
        raise SettingValidationError("settings", "must be an object")

    validated = dict(updates)
    for key, value in validated.items():
        spec = schema.get(key)
        if spec is None:
            raise SettingValidationError(key, f"unknown setting for service {service}")
        _validate_basic(key, value, spec)
        if key == "pid_limits":
            _validate_pid_limits(value)

    effective = dict(current or {})
    effective.update(validated)

    if "safety_limits" in validated:
        _validate_safety_limits(validated["safety_limits"])

    if "pid_defaults" in validated or "pid_limits" in validated:
        defaults = effective.get("pid_defaults")
        if defaults is not None:
            if not isinstance(defaults, dict):
                raise SettingValidationError("pid_defaults", "must be an object")
            _validate_pid_defaults(defaults, effective.get("pid_limits"))

    return validated


def _cache_path(service: str, cache_path: str | Path | None) -> Path:
    _require_service(service)
    return Path(cache_path) if cache_path is not None else SETTINGS_CACHE_DIR / f"{service}.json"


def write_cache(path: str | Path, values: Mapping[str, Any]) -> None:
    """Atomically replace a JSON settings cache using a sibling temporary file."""
    cache_file = Path(path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=cache_file.parent,
            prefix=f".{cache_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(dict(values), temporary_file, sort_keys=True, separators=(",", ":"))
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, cache_file)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as cache_file:
            cached = json.load(cache_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Ignoring unreadable service settings cache at %s", path, exc_info=True)
        return {}

    if not isinstance(cached, dict):
        logger.warning("Ignoring non-object service settings cache at %s", path)
        return {}
    return cached


def _decode_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def _read_db_values(pool: Any, service: str) -> dict[str, Any] | None:
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM public.service_setting WHERE service = $1", service
        )
    except Exception:
        logger.warning(
            "Unable to load %s service settings from the database", service, exc_info=True
        )
        return None

    return {row["key"]: _decode_jsonb(row["value"]) for row in rows}


async def _load_resolved(
    pool: Any,
    service: str,
    *,
    cache_path: str | Path | None,
    fallback: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    path = _cache_path(service, cache_path)
    cached_values = _read_cache(path)
    database_values = await _read_db_values(pool, service)
    if database_values is None:
        database_values = {}
        database_available = False
    else:
        database_available = True

    cached_snapshot = dict(cached_values)
    cached_snapshot.update(database_values)
    if database_available and database_values:
        try:
            write_cache(path, cached_snapshot)
        except OSError:
            logger.warning("Unable to update %s service settings cache", service, exc_info=True)

    effective = dict(fallback or {})
    effective.update(cached_values)
    effective.update(database_values)
    return effective, database_available


class ServiceSettings:
    """Async loader and change poller for one or more settings-backed services."""

    @staticmethod
    async def load(
        pool: Any,
        service: str,
        *,
        cache_path: str | Path | None = None,
        fallback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve settings in database, cache, then fallback order."""
        values, _ = await _load_resolved(
            pool,
            service,
            cache_path=cache_path,
            fallback=fallback,
        )
        return values

    @staticmethod
    async def run_refresh(
        pool: Any,
        service: str,
        on_change: Callable[[dict[str, Any]], Awaitable[Any] | Any],
        interval_s: float = 10.0,
        *,
        cache_path: str | Path | None = None,
        fallback: Mapping[str, Any] | None = None,
    ) -> None:
        """Poll the service's newest ``updated_at`` and apply changed values."""
        _require_service(service)
        if interval_s < 0:
            raise ValueError("interval_s must be non-negative")

        try:
            last_updated_at = await pool.fetchval(
                "SELECT max(updated_at) FROM public.service_setting WHERE service = $1", service
            )
        except Exception:
            logger.warning("Unable to initialize %s settings refresh", service, exc_info=True)
            last_updated_at = None

        while True:
            await asyncio.sleep(interval_s)
            try:
                updated_at = await pool.fetchval(
                    "SELECT max(updated_at) FROM public.service_setting WHERE service = $1", service
                )
            except Exception:
                logger.warning("Unable to poll %s settings refresh", service, exc_info=True)
                continue

            if updated_at == last_updated_at:
                continue

            values, database_available = await _load_resolved(
                pool,
                service,
                cache_path=cache_path,
                fallback=fallback,
            )
            if not database_available:
                continue

            result = on_change(values)
            if inspect.isawaitable(result):
                await result
            last_updated_at = updated_at


__all__ = [
    "SETTINGS_CACHE_DIR",
    "SETTINGS_SCHEMA",
    "ServiceSettings",
    "SettingSpec",
    "SettingValidationError",
    "UnknownServiceError",
    "validate_settings",
    "write_cache",
]
