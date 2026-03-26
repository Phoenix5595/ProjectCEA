from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import redis as redis_lib

"""Redis key schema constants for ProjectCEA automation service."""
OLD_KEY_PATTERNS: list[str] = [
    # Sensor last-good values
    "sensor:{cluster}:{sensor_name}:last_good",
    # Modes
    "mode:{location}:{cluster}",
    # Setpoints (per location/cluster)
    "setpoint:{location}:{cluster}:{name}",
    "setpoint:{location}:{cluster}:source",
    # Effective setpoints
    "effective_setpoint:{location}:{cluster}:{name}",
    # Schedule state
    "schedule:state:{location}:{cluster}",
    # Ramp state keys
    "ramp:{location}:{cluster}:{setpoint_type}",
    "ramp_persist:{location}:{cluster}:{setpoint_type}",
    # Alarms
    "alarm:{location}:{cluster}:{alarm_name}",
    # PID
    "pid:parameters:{device_type}",
    # Heartbeat
    "heartbeat:{service_name}",
]

# 2) NEW_KEY_PATTERNS: standardized cea-prefixed patterns
NEW_KEY_PATTERNS: list[str] = [
    # Sensor last-good values (example detail: last_good)
    "cea:sensor:{location}:{cluster}:{sensor_name}_last_good",
    # Mode
    "cea:mode:{location}:{cluster}",
    # Setpoints
    "cea:setpoint:{location}:{cluster}:{name}",
    # Effective setpoints
    "cea:effective_setpoint:{location}:{cluster}:{name}",
    # Schedule state
    "cea:schedule:{location}:{cluster}:state",
    # Ramp state
    "cea:ramp:{location}:{cluster}:{setpoint_type}",
    "cea:ramp_persist:{location}:{cluster}:{setpoint_type}",
    # Alarms
    "cea:alarm:{location}:{cluster}:{alarm_name}",
    # PID
    "cea:pid:{device_type}",
    # Heartbeat
    "cea:heartbeat:{service_name}",
]

# 3) MIGRATION_MAP: old pattern -> new pattern template
# Note: these templates are guidance for migrating data; exact runtime-logic for
# migration is out of scope for this task.
MIGRATION_MAP: dict[str, str] = {
    "sensor:{cluster}:{sensor_name}:last_good": "cea:sensor:{location}:{cluster}:{sensor_name}_last_good",
    "mode:{location}:{cluster}": "cea:mode:{location}:{cluster}",
    "setpoint:{location}:{cluster}:{name}": "cea:setpoint:{location}:{cluster}:{name}",
    "setpoint:{location}:{cluster}:source": "cea:setpoint:{location}:{cluster}:source",
    "effective_setpoint:{location}:{cluster}:{name}": "cea:effective_setpoint:{location}:{cluster}:{name}",
    "schedule:state:{location}:{cluster}": "cea:schedule:{location}:{cluster}:state",
    "ramp:{location}:{cluster}:{setpoint_type}": "cea:ramp:{location}:{cluster}:{setpoint_type}",
    "ramp_persist:{location}:{cluster}:{setpoint_type}": "cea:ramp_persist:{location}:{cluster}:{setpoint_type}",
    "alarm:{location}:{cluster}:{alarm_name}": "cea:alarm:{location}:{cluster}:{alarm_name}",
    "pid:parameters:{device_type}": "cea:pid:{device_type}",
    "heartbeat:{service_name}": "cea:heartbeat:{service_name}",
}


# 4) Helper helpers to build keys consistently
def build_key(
    entity: str, location: str | None = None, cluster: str | None = None, detail: str | None = None
) -> str:
    """
    Build a new cea-prefixed key with the canonical shape:
    cea:{entity}:{location}:{cluster}:{detail}
    - location and cluster default to 'global'/'default' if not provided.
    - detail is optional.
    """
    loc = location or "global"
    clu = cluster or "default"
    if detail:
        return f"cea:{entity}:{loc}:{clu}:{detail}"
    return f"cea:{entity}:{loc}:{clu}"


def sensor_key(location: str, cluster: str, sensor_name: str, suffix: str = "last_good") -> str:
    return build_key("sensor", location, cluster, f"{sensor_name}_{suffix}")


def mode_key(location: str, cluster: str) -> str:
    return build_key("mode", location, cluster, None)


def setpoint_key(location: str, cluster: str, name: str) -> str:
    return build_key("setpoint", location, cluster, name)


def effective_setpoint_key(location: str, cluster: str, name: str) -> str:
    return build_key("effective_setpoint", location, cluster, name)


def schedule_key(location: str, cluster: str) -> str:
    return build_key("schedule", location, cluster, "state")


def ramp_key(location: str, cluster: str, setpoint_type: str) -> str:
    return build_key("ramp", location, cluster, setpoint_type)


def ramp_persist_key(location: str, cluster: str, setpoint_type: str) -> str:
    return build_key("ramp_persist", location, cluster, setpoint_type)


def alarm_key(location: str, cluster: str, alarm_name: str) -> str:
    return build_key("alarm", location, cluster, alarm_name)


def pid_key(device_type: str) -> str:
    # Pid keys historically had no location/cluster, map to a global/default layout
    return build_key("pid", "global", "default", device_type)


def heartbeat_key(service_name: str) -> str:
    return build_key("heartbeat", None, None, service_name)


# Backward-compatibility helpers for gradual key migration
def get_with_backward_compat(
    redis_client: redis_lib.Redis,
    old_key_pattern: str,
    new_key_func: Callable[..., str],
    *args: Any,
    **kwargs: Any,
) -> Any:
    # Try new key first
    new_key = new_key_func(*args, **kwargs)
    value = redis_client.get(new_key)
    if value:
        return value

    # Fall back to old key
    old_key = old_key_pattern.format(*args, **kwargs)
    return redis_client.get(old_key)


def set_with_backward_compat(
    redis_client: redis_lib.Redis,
    old_key_pattern: str,
    new_key_func: Callable[..., str],
    value: str | bytes,
    ttl: int | None = None,
    *args: Any,
    **kwargs: Any,
) -> None:
    # Write to new key (primary)
    new_key = new_key_func(*args, **kwargs)
    if ttl:
        redis_client.setex(new_key, ttl, value)
    else:
        redis_client.set(new_key, value)

    # Also write to old key for backward compat
    old_key = old_key_pattern.format(*args, **kwargs)
    if ttl:
        redis_client.setex(old_key, ttl, value)
    else:
        redis_client.set(old_key, value)


# Exported __all__ for explicit API
__all__ = [
    "OLD_KEY_PATTERNS",
    "NEW_KEY_PATTERNS",
    "MIGRATION_MAP",
    "build_key",
    "sensor_key",
    "mode_key",
    "setpoint_key",
    "effective_setpoint_key",
    "schedule_key",
    "ramp_key",
    "ramp_persist_key",
    "alarm_key",
    "pid_key",
    "heartbeat_key",
    "get_with_backward_compat",
    "set_with_backward_compat",
]
