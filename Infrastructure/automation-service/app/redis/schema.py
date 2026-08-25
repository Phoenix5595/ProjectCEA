from __future__ import annotations

from shared.redis_keys import sensor_last_good

"""Redis key schema constants for ProjectCEA automation service."""

# Canonical cea-prefixed key patterns (consumed by SchemaValidationMixin).
NEW_KEY_PATTERNS: list[str] = [
    # Sensor last-good values
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


# Global non-patterned keys (no location/cluster dimension)
RELAY_BOARD_SNAPSHOT = "cea:relay:board_snapshot"


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
    """Build the canonical last-good key while retaining the legacy call signature."""
    if suffix == "last_good":
        return sensor_last_good(location, cluster, sensor_name)
    return build_key("sensor", location, cluster, f"{sensor_name}_{suffix}")


def mode_key(location: str, cluster: str) -> str:
    return build_key("mode", location, cluster, None)


def setpoint_key(location: str, cluster: str, name: str) -> str:
    return build_key("setpoint", location, cluster, name)


def effective_setpoint_key(location: str, cluster: str, name: str) -> str:
    return build_key("effective_setpoint", location, cluster, name)


def effective_setpoint_prefix(location: str, cluster: str) -> str:
    return f"cea:effective_setpoint:{location}:{cluster}"


def effective_setpoint_light_field_key(
    location: str, cluster: str, device_name: str, field: str
) -> str:
    """Canonical cea key for per-dimmer light effective/nominal/ramp in Redis.

    ``field`` is one of: ``effective_intensity``, ``nominal_intensity``, ``ramp_progress_light``.
    """
    return f"{effective_setpoint_prefix(location, cluster)}:light:{device_name}:{field}"


def light_state_key(location: str, cluster: str, device_name: str) -> str:
    return build_key("light", location, cluster, device_name)


def automation_state_key(location: str, cluster: str, device_name: str) -> str:
    return build_key("automation", location, cluster, device_name)


def alarm_prefix(location: str, cluster: str) -> str:
    return f"cea:alarm:{location}:{cluster}:"


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


def schedule_cache_key(location: str, cluster: str) -> str:
    return f"schedules:loc:{location}:cluster:{cluster}"


def pid_key_with_location(location: str, cluster: str, device_type: str) -> str:
    return f"cea:pid:{location}:{cluster}:{device_type}"


def pid_all_parameters_key() -> str:
    return build_key("pid", "global", "default", "all")


def alarm_pattern(location: str, cluster: str) -> str:
    return f"cea:alarm:{location}:{cluster}:*"


def legacy_failsafe_key(location: str, cluster: str) -> str:
    return f"failsafe:{location}:{cluster}"


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


# Exported __all__ for explicit API
__all__ = [
    "NEW_KEY_PATTERNS",
    "RELAY_BOARD_SNAPSHOT",
    "build_key",
    "sensor_key",
    "mode_key",
    "setpoint_key",
    "effective_setpoint_key",
    "effective_setpoint_prefix",
    "effective_setpoint_light_field_key",
    "light_state_key",
    "automation_state_key",
    "schedule_key",
    "ramp_key",
    "ramp_persist_key",
    "alarm_key",
    "alarm_prefix",
    "pid_key",
    "heartbeat_key",
    "relay_raw_override_key",
    "schedule_cache_key",
    "pid_key_with_location",
    "pid_all_parameters_key",
    "alarm_pattern",
    "legacy_failsafe_key",
    "pid_autotune_key",
    "pid_autotune_key_with_location",
    "schedule_cache_key_all",
    "schedule_cache_key_location",
    "schedule_cache_key_light",
    "schedule_cache_key_room_light",
]
