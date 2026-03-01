"""
TTL configuration for Redis keys used by the automation service.

Categories (TTL strategy):
- CRITICAL: No TTL (no expiration). Used for mode/failsafe data.
- RUNTIME: Short-lived data during runtime (60 seconds).
- TRANSIENT: Very short-lived data (10 seconds) for volatile sensor values.
- CACHED: Longer-lived data (300 seconds) for stable configuration data.
"""

from __future__ import annotations

from enum import Enum


class TTLCategory(Enum):
    CRITICAL = "CRITICAL"
    RUNTIME = "RUNTIME"
    TRANSIENT = "TRANSIENT"
    CACHED = "CACHED"


# TTL values in seconds per category. Use None for CRITICAL to denote no expiration.
TTL_SECONDS: dict[TTLCategory, int | None] = {
    TTLCategory.CRITICAL: None,  # No TTL (no automatic expiration)
    TTLCategory.RUNTIME: 60,  # Runtime data (setpoints, ramps, etc.)
    TTLCategory.TRANSIENT: 10,  # Transient data (sensor values, etc.)
    TTLCategory.CACHED: 300,  # Cached data (schedules, PID params, etc.)
}


# Map specific Redis key types or logical data categories to TTL categories.
KEY_TYPE_CATEGORY_MAP: dict[str, TTLCategory] = {
    # Critical / failsafe data
    "mode": TTLCategory.CRITICAL,
    "failsafe": TTLCategory.CRITICAL,
    # Runtime control data
    "setpoint": TTLCategory.RUNTIME,
    "ramps": TTLCategory.RUNTIME,
    # Transient sensor data
    "sensor_value": TTLCategory.TRANSIENT,
    # Add common sensor keys if needed in the future
    # Cached/stable configuration
    "schedule": TTLCategory.CACHED,
    "pid_param": TTLCategory.CACHED,
    "config": TTLCategory.CACHED,
}


def ttl_for_category(category: TTLCategory) -> int | None:
    """Return TTL in seconds for a given TTL category. None means no expiration."""
    return TTL_SECONDS.get(category)


def get_ttl_by_key_type(key_type: str) -> int | None:
    """Return TTL seconds for a given Redis key type string.

    If the key_type is not recognized, returns None to indicate no default TTL
    (caller should decide on a sane fallback).
    """
    category = KEY_TYPE_CATEGORY_MAP.get(key_type)
    if category is None:
        return None
    return ttl_for_category(category)


__all__ = [
    "TTLCategory",
    "TTL_SECONDS",
    "KEY_TYPE_CATEGORY_MAP",
    "ttl_for_category",
    "get_ttl_by_key_type",
]
