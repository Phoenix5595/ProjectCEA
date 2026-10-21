"""Canonical Redis key/stream names and retention policy for Project CEA.

This module is the single source of truth for every Redis key, stream, or
pubsub channel touched by any service. All NEW code MUST go through the
builders and constants exported here. Existing call-sites are being migrated
incrementally — do not add new literal-string keys
to the codebase.

Three design principles
-----------------------
1. **Names are opaque — never string-concatenate them at the call site.**
   Use a builder function. Keys embed ``location``/``cluster``/``sensor_name``
   which may contain spaces (e.g. ``"Veg Room"``) and that's fine — Redis is
   byte-safe. We don't URL-encode or substitute spaces; every service already
   round-trips them unchanged.

2. **One namespace per concept.**
    - ``cea:sensor:*`` — current sensor values (state, not history).
   - ``schedules:*``  — schedule JSON docs by location/cluster.
   - ``cea:schedule:*`` — legacy + schedule state.
   - ``automation:*`` — last-issued automation decision per device.
   - ``failsafe:*``   — per-cluster failsafe-active flag.
    - ``sensor:raw``   — main XADD telemetry stream (CAN + soil + 1-wire).
    - ``cea:events:operational`` — bounded observability-only event timeline.

3. **Retention is a property of the stream, not the writer.**
    Retention limits are defined by the corresponding ``*_MAXLEN`` constants.
    Writers pass ``approximate=True`` so Redis can trim in buckets of ~100
    entries, which is roughly free.

If you are adding a NEW kind of key, pick a namespace from the list above
and add a builder here. Don't invent a new top-level namespace without a
corresponding entry in Infrastructure/REQUIREMENTS.md.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Streams (XADD)
# ---------------------------------------------------------------------------

SENSOR_RAW_STREAM: Final[str] = "sensor:raw"
"""Main telemetry stream. Producers: can-processor, soil-sensor, onewire.
Consumers: backend (live readings), automation (setpoint math)."""


CONFIG_EVENTS_STREAM: Final[str] = "cea:events:config"
"""Cross-service configuration event stream.
Producer: automation-service event bus. Consumer: backend config-event
consumer. Events are notifications; durable configuration remains in the
authoritative configuration store."""

OPERATIONAL_EVENTS_STREAM: Final[str] = "cea:events:operational"
"""Global observability-only stream of typed operational events."""

SENSOR_RAW_MAXLEN: Final[int] = 100_000
"""Retention for the sensor:raw stream. At the observed ~3 msg/s CAN rate
this is ~9 hours of buffer — enough for any stream-reader to reconnect and
catch up after a transient outage. Historical durability is owned by
TimescaleDB, not by this stream."""


CONFIG_EVENTS_MAXLEN: Final[int] = 10_000
"""Bounded retention for ``cea:events:config``.
The backend consumer group processes configuration-change notifications; the
stream is not the source of truth for configuration, so a finite reconnect
buffer prevents an unbounded operational queue."""

OPERATIONAL_EVENTS_RETENTION_MS: Final[int] = 86_400_000
"""24-hour Redis stream age bound in milliseconds for operational events."""

OPERATIONAL_EVENTS_MAXLEN: Final[int] = 50_000
"""Exact maximum retained entry count for the operational event stream."""

OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES: Final[int] = 4_096
"""Maximum UTF-8 byte length of one serialized operational event."""


# ---------------------------------------------------------------------------
# Pubsub channels (PUBLISH / SUBSCRIBE)
# ---------------------------------------------------------------------------

SENSOR_UPDATE_CHANNEL: Final[str] = "sensor:update"
"""Best-effort notification that a sensor value changed. Subscribers should
NOT rely on receiving every message — this is a wake-up signal, not a bus."""

SENSOR_UPDATE_SOIL_CHANNEL: Final[str] = "sensor:update:soil"
"""Soil-specific variant of SENSOR_UPDATE_CHANNEL."""


# ---------------------------------------------------------------------------
# Sensor state keys
# ---------------------------------------------------------------------------


def sensor_full(location: str, cluster: str, sensor_type: str) -> str:
    """``cea:sensor:{location}:{cluster}:{sensor_type}`` — fully-qualified
    current value. This is the form the automation service reads when
    evaluating setpoints, so the writer must populate it for every
    (location, cluster, sensor_type) triple the topology declares."""
    return f"cea:sensor:{location}:{cluster}:{sensor_type}"


def sensor_full_ts(location: str, cluster: str, sensor_type: str) -> str:
    """``cea:sensor:{location}:{cluster}:{sensor_type}_ts`` — epoch timestamp
    in milliseconds for ``sensor_full()``. Matches the underscore-suffix
    convention used by the automation reader (NOT a colon — do not 'fix')."""
    return f"cea:sensor:{location}:{cluster}:{sensor_type}_ts"


# ---------------------------------------------------------------------------
# Schedule keys
# ---------------------------------------------------------------------------


def climate_period_cache_key(
    location: str, cluster: str, mode_id: int | None, submode_id: int | None = None
) -> str:
    """Exact-profile climate period resolver cache key.

    ``cache:climate_period:{location}:{cluster}:mode:{mode_id}:submode:{submode
    _id-or-none}`` — invalidation deletes one key per affected identity; a
    ``None`` submode spells the literal ``none`` so NULL-base profiles always
    map to one key.
    """
    return (
        f"cache:climate_period:{location}:{cluster}"
        f":mode:{mode_id}:submode:{'none' if submode_id is None else submode_id}"
    )


# ---------------------------------------------------------------------------
# TTL policy
# ---------------------------------------------------------------------------

# Current sensor state has source-specific freshness semantics. Do not replace
# these with a universal default: readers must observe the policy of the
# source that produced the value.

CAN_SENSOR_CURRENT_TTL_SEC: Final[None] = None
"""No TTL. CAN current values and timestamps persist until the next frame.
This preserves the existing database-fallback avoidance policy."""

SOIL_SENSOR_CURRENT_TTL_SEC: Final[int] = 10
"""10 s. Soil sensor values and timestamps expire when polling freshness is
lost, allowing consumers to detect unavailable Modbus data."""

ONEWIRE_SENSOR_CURRENT_TTL_SEC: Final[int] = 10
"""10 s. 1-Wire values and timestamps expire when probe polling freshness is
lost, allowing consumers to detect unavailable hardware."""


def monitoring_current_publication_key(location: str) -> str:
    """Canonical current-fact publication key for one automation location."""
    return f"cea:monitoring:current:{location}"


def monitoring_future_publication_key(location: str) -> str:
    """Canonical future-projection publication key for one automation location."""
    return f"cea:monitoring:future:{location}"


def monitoring_rich_trajectory_key(location: str) -> str:
    return f"cea:monitoring:trajectory:{location}"


__all__ = [
    # streams
    "SENSOR_RAW_STREAM",
    "CONFIG_EVENTS_STREAM",
    "OPERATIONAL_EVENTS_STREAM",
    "SENSOR_RAW_MAXLEN",
    "CONFIG_EVENTS_MAXLEN",
    "OPERATIONAL_EVENTS_RETENTION_MS",
    "OPERATIONAL_EVENTS_MAXLEN",
    "OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES",
    # pubsub
    "SENSOR_UPDATE_CHANNEL",
    "SENSOR_UPDATE_SOIL_CHANNEL",
    # sensor keys
    "sensor_full",
    "sensor_full_ts",
    # ttl constants
    "CAN_SENSOR_CURRENT_TTL_SEC",
    "SOIL_SENSOR_CURRENT_TTL_SEC",
    "ONEWIRE_SENSOR_CURRENT_TTL_SEC",
    # monitoring publications
    "monitoring_current_publication_key",
    "monitoring_future_publication_key",
    "monitoring_rich_trajectory_key",
]
