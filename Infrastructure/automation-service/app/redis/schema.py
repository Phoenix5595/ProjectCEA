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
    # Effective setpoints (climate + per-light under :light:{device}:…)
    "effective_setpoint:{location}:{cluster}:{name}",
    "effective_setpoint:{location}:{cluster}:light:{device_name}:{field}",
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


# 4) Global non-patterned keys (no location/cluster dimension)
RELAY_CHANNELS = "cea:relay:channels"
RELAY_TIMESTAMPS = "cea:relay:timestamps"


# 5) Helper helpers to build keys consistently
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


def effective_setpoint_light_field_key(
    location: str, cluster: str, device_name: str, field: str
) -> str:
    """Legacy non-cea key for per-dimmer light effective/nominal/ramp in Redis.

    ``field`` is one of: ``effective_intensity``, ``nominal_intensity``, ``ramp_progress_light``.
    """
    return f"effective_setpoint:{location}:{cluster}:light:{device_name}:{field}"


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


def relay_raw_override_key(channel: int) -> str:
    return f"cea:relay:manual_override:{channel}"


def light_state_key(location: str, cluster: str, device_name: str) -> str:
    return f"cea:light:{location}:{cluster}:{device_name}"


def automation_state_key(location: str, cluster: str, device_name: str) -> str:
    return f"cea:automation:{location}:{cluster}:{device_name}"


def schedule_cache_key(location: str, cluster: str) -> str:
    return f"schedules:loc:{location}:cluster:{cluster}"


def effective_setpoint_light_key(location: str, cluster: str, device_name: str) -> str:
    return f"effective_setpoint:{location}:{cluster}:{device_name}:light"


def pid_key_with_location(location: str, cluster: str, device_type: str) -> str:
    return f"cea:pid:{location}:{cluster}:{device_type}"


def legacy_light_state_key(location: str, cluster: str, device_name: str) -> str:
    return f"light:{location}:{cluster}:{device_name}"


def legacy_automation_state_key(location: str, cluster: str, device_name: str) -> str:
    return f"automation:{location}:{cluster}:{device_name}"


def legacy_setpoint_field_key(location: str, cluster: str, field: str) -> str:
    return f"setpoint:{location}:{cluster}:{field}"


def legacy_effective_setpoint_prefix(location: str, cluster: str) -> str:
    return f"effective_setpoint:{location}:{cluster}"


def legacy_alarm_key(location: str, cluster: str, alarm_name: str) -> str:
    return f"alarm:{location}:{cluster}:{alarm_name}"


def legacy_alarm_prefix(location: str, cluster: str) -> str:
    return f"alarm:{location}:{cluster}:"


def legacy_alarm_pattern(location: str, cluster: str) -> str:
    return f"alarm:{location}:{cluster}:*"


def alarm_pattern(location: str, cluster: str) -> str:
    return f"cea:alarm:{location}:{cluster}:*"


def legacy_ramp_key(location: str, cluster: str, setpoint_type: str) -> str:
    return f"ramp:{location}:{cluster}:{setpoint_type}"


def legacy_ramp_persist_key(location: str, cluster: str, setpoint_type: str) -> str:
    return f"ramp_persist:{location}:{cluster}:{setpoint_type}"


def legacy_mode_key(location: str, cluster: str) -> str:
    return f"mode:{location}:{cluster}"


def legacy_failsafe_key(location: str, cluster: str) -> str:
    return f"failsafe:{location}:{cluster}"


def pid_parameters_key(location: str, cluster: str, device_type: str) -> str:
    return f"pid:parameters:{location}:{cluster}:{device_type}"


def pid_autotune_key(device_type: str) -> str:
    return f"pid:autotune:{device_type}"


def pid_autotune_key_with_location(location: str, cluster: str, device_type: str) -> str:
    return f"pid:autotune:{location}:{cluster}:{device_type}"


def schedule_cache_key_all() -> str:
    return "schedules:all"


def schedule_cache_key_location(location: str) -> str:
    return f"schedules:loc:{location}"


def schedule_cache_key_light(location: str, cluster: str, device_name: str) -> str:
    return f"schedules:loc:{location}:cluster:{cluster}:light:{device_name}"


def schedule_cache_key_room_light(location: str, cluster: str) -> str:
    return f"schedules:loc:{location}:cluster:{cluster}:room_light_schedule"


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
    "RELAY_CHANNELS",
    "RELAY_TIMESTAMPS",
    "build_key",
    "sensor_key",
    "mode_key",
    "setpoint_key",
    "effective_setpoint_key",
    "effective_setpoint_light_field_key",
    "schedule_key",
    "ramp_key",
    "ramp_persist_key",
    "alarm_key",
    "pid_key",
    "heartbeat_key",
    "relay_raw_override_key",
    "light_state_key",
    "automation_state_key",
    "schedule_cache_key",
    "effective_setpoint_light_key",
    "pid_key_with_location",
    "legacy_light_state_key",
    "legacy_automation_state_key",
    "legacy_setpoint_field_key",
    "legacy_effective_setpoint_prefix",
    "legacy_alarm_key",
    "legacy_alarm_prefix",
    "legacy_alarm_pattern",
    "alarm_pattern",
    "legacy_ramp_key",
    "legacy_ramp_persist_key",
    "legacy_mode_key",
    "legacy_failsafe_key",
    "pid_parameters_key",
    "pid_autotune_key",
    "pid_autotune_key_with_location",
    "schedule_cache_key_all",
    "schedule_cache_key_location",
    "schedule_cache_key_light",
    "schedule_cache_key_room_light",
    "get_with_backward_compat",
    "set_with_backward_compat",
]
