"""Canonical Redis key/stream names and retention policy for Project CEA.

This module is the single source of truth for every Redis key, stream, or
pubsub channel touched by any service. All NEW code MUST go through the
builders and constants exported here. Existing call-sites are being migrated
incrementally (see `LEGACY NOTES` below) — do not add new literal-string keys
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
    - ``sensor:*``     — legacy compatibility form (see LEGACY NOTES).
   - ``schedules:*``  — schedule JSON docs by location/cluster.
   - ``cea:schedule:*`` — legacy + schedule state (see LEGACY NOTES).
   - ``automation:*`` — last-issued automation decision per device.
   - ``failsafe:*``   — per-cluster failsafe-active flag.
   - ``sensor:raw``   — main XADD telemetry stream (CAN + soil + 1-wire).
   - ``stream:control`` — XADD stream of effective setpoint decisions.

3. **Retention is a property of the stream, not the writer.**
    ``SENSOR_RAW_MAXLEN``, ``CONTROL_STREAM_MAXLEN``, and
    ``CONFIG_EVENTS_MAXLEN`` are the only numbers that should appear as
    ``maxlen=`` arguments. Writers pass
   ``approximate=True`` so Redis can trim in buckets of ~100 entries, which
   is roughly free.

LEGACY NOTES (do not port to new code)
--------------------------------------
Two parallel sensor schemes exist in production today:

- short form    : ``sensor:{name}`` / ``sensor:{name}:ts``
                  (legacy current-value + write-timestamp compatibility)
- full form     : ``cea:sensor:{location}:{cluster}:{sensor_type}`` (+ ``_ts``)
                  (the canonical topology-qualified contract)

The short form remains available only for the compatibility paths retained
until Task 23. New readers and writers use the ``cea:sensor:`` contract.

Three parallel schedule-state schemes exist too:
``cea:schedule:{location}:{cluster}:state``,
``cea:schedule:state:{location}:{cluster}``, and ``schedule:state:{loc}:{cl}``.
Same story — writers and readers are paired per service, nothing is broken,
but a future lift will pick one. Builders here expose ALL THREE explicitly
so grep-ability is preserved.

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

CONTROL_STREAM: Final[str] = "stream:control"
"""Effective-setpoint decisions emitted by the automation control loop.
Producer: automation-service/app/redis/setpoints.py.
Consumer: (reserved for future ops/debug dashboards)."""

CONFIG_EVENTS_STREAM: Final[str] = "cea:events:config"
"""Cross-service configuration event stream.
Producer: automation-service event bus. Consumer: backend config-event
consumer. Events are notifications; durable configuration remains in the
authoritative configuration store."""

SENSOR_RAW_MAXLEN: Final[int] = 100_000
"""Retention for the sensor:raw stream. At the observed ~3 msg/s CAN rate
this is ~9 hours of buffer — enough for any stream-reader to reconnect and
catch up after a transient outage. Historical durability is owned by
TimescaleDB, not by this stream."""

CONTROL_STREAM_MAXLEN: Final[int] = 100_000
"""Same retention envelope as SENSOR_RAW_MAXLEN for consistency.
Control-loop ticks are ~0.3 Hz so this is weeks of buffer; trimming keeps
memory bounded if a dashboard ever subscribes and falls behind."""

CONFIG_EVENTS_MAXLEN: Final[int] = 10_000
"""Bounded retention for ``cea:events:config``.
The backend consumer group processes configuration-change notifications; the
stream is not the source of truth for configuration, so a finite reconnect
buffer prevents an unbounded operational queue."""

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


def sensor_short(sensor_name: str) -> str:
    """``sensor:{name}`` — legacy single current value.

    Retained only for Task-23 compatibility code. Paired with
    ``sensor_short_ts()`` for the legacy write-timestamp companion key.
    """
    return f"sensor:{sensor_name}"


def sensor_short_ts(sensor_name: str) -> str:
    """``sensor:{name}:ts`` — ISO-8601 timestamp of the last write to
    ``sensor_short(name)``."""
    return f"sensor:{sensor_name}:ts"


def sensor_full(location: str, cluster: str, sensor_type: str) -> str:
    """``cea:sensor:{location}:{cluster}:{sensor_type}`` — fully-qualified
    current value. This is the form the automation service reads when
    evaluating setpoints, so the writer must populate it for every
    (location, cluster, sensor_type) triple the topology declares."""
    return f"cea:sensor:{location}:{cluster}:{sensor_type}"


def sensor_full_ts(location: str, cluster: str, sensor_type: str) -> str:
    """``cea:sensor:{location}:{cluster}:{sensor_type}_ts`` — ISO-8601 ts for
    ``sensor_full()``. Matches the underscore-suffix convention used by the
    existing automation reader (NOT a colon — do not 'fix' this)."""
    return f"cea:sensor:{location}:{cluster}:{sensor_type}_ts"


def sensor_last_good(location: str, cluster: str, sensor_name: str) -> str:
    """Last-known-good cached value, used during short sensor outages so the
    control loop doesn't immediately enter failsafe.

    Canonical form: ``cea:sensor:{location}:{cluster}:{sensor_name}_last_good``
    """
    return f"cea:sensor:{location}:{cluster}:{sensor_name}_last_good"


# ---------------------------------------------------------------------------
# Schedule keys
# ---------------------------------------------------------------------------


def schedule_doc_all() -> str:
    """``schedules:all`` — SET of every schedule key. Used by maintenance
    scripts to iterate without SCAN."""
    return "schedules:all"


def schedule_doc_location(location: str) -> str:
    """``schedules:loc:{location}`` — location-level schedule envelope."""
    return f"schedules:loc:{location}"


def schedule_doc_cluster(location: str, cluster: str) -> str:
    """``schedules:loc:{location}:cluster:{cluster}`` — cluster-level
    schedule envelope (contains climate + light children)."""
    return f"schedules:loc:{location}:cluster:{cluster}"


def schedule_doc_climate(location: str, cluster: str) -> str:
    """``schedules:loc:{location}:cluster:{cluster}:climate`` — climate
    schedule JSON for a (location, cluster)."""
    return f"schedules:loc:{location}:cluster:{cluster}:climate"


def schedule_doc_light(location: str, cluster: str, device_name: str) -> str:
    """``schedules:loc:{location}:cluster:{cluster}:light:{device_name}`` —
    per-device light schedule JSON."""
    return f"schedules:loc:{location}:cluster:{cluster}:light:{device_name}"


def schedule_doc_room_schedule(location: str, cluster: str) -> str:
    """``schedules:loc:{location}:cluster:{cluster}:room_schedule`` —
    aggregate room schedule."""
    return f"schedules:loc:{location}:cluster:{cluster}:room_schedule"


def schedule_doc_room_light_schedule(location: str, cluster: str) -> str:
    """``schedules:loc:{location}:cluster:{cluster}:room_light_schedule``."""
    return f"schedules:loc:{location}:cluster:{cluster}:room_light_schedule"


# Legacy singular schedule keys (cea:schedule:*). Retained so a grep
# across the codebase always lands here. Do not use for new code.


def schedule_state_legacy(location: str, cluster: str) -> str:
    """``cea:schedule:{location}:{cluster}:state`` — legacy suffix form.
    Writer: automation-service; reader: backend status endpoint."""
    return f"cea:schedule:{location}:{cluster}:state"


def schedule_state_short(location: str, cluster: str) -> str:
    """``schedule:state:{location}:{cluster}`` — third competing form
    without the ``cea:`` prefix. Present in older automation code paths."""
    return f"schedule:state:{location}:{cluster}"


# ---------------------------------------------------------------------------
# Automation + failsafe keys
# ---------------------------------------------------------------------------


def failsafe_active(location: str, cluster: str) -> str:
    """``failsafe:{location}:{cluster}`` — 1 / 0 flag; when set, the
    automation control loop is paused for that cluster and devices fall
    back to their safe-default outputs."""
    return f"failsafe:{location}:{cluster}"


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

SENSOR_LAST_GOOD_TTL_SEC: Final[int] = 0
"""No TTL. Last-good values persist until explicitly overwritten so the
control loop can ride out arbitrary sensor outages without entering
failsafe."""

FAILSAFE_TTL_SEC: Final[int] = 0
"""No TTL. Failsafe is explicitly cleared by whoever set it — never time-
expired. If a failsafe writer dies before clearing, the operator must clear
it manually (documented in REQUIREMENTS.md)."""

SCHEDULE_DOC_TTL_SEC: Final[int] = 0
"""No TTL. Schedules are authoritative in Redis (with Postgres as cold
backup) and must survive service restarts."""

AUTOMATION_DECISION_TTL_SEC: Final[int] = 3600
"""1 hour. Automation decisions are useful for observability but are not
authoritative — if they disappear, the control loop will simply re-emit
them on the next tick."""


def monitoring_current_publication_key(location: str) -> str:
    """Canonical current-fact publication key for one automation location."""
    return f"cea:monitoring:current:{location}"


def monitoring_future_publication_key(location: str) -> str:
    """Canonical future-projection publication key for one automation location."""
    return f"cea:monitoring:future:{location}"


__all__ = [
    # streams
    "SENSOR_RAW_STREAM",
    "CONTROL_STREAM",
    "CONFIG_EVENTS_STREAM",
    "SENSOR_RAW_MAXLEN",
    "CONTROL_STREAM_MAXLEN",
    "CONFIG_EVENTS_MAXLEN",
    # pubsub
    "SENSOR_UPDATE_CHANNEL",
    "SENSOR_UPDATE_SOIL_CHANNEL",
    # sensor keys
    "sensor_short",
    "sensor_short_ts",
    "sensor_full",
    "sensor_full_ts",
    "sensor_last_good",
    # schedule keys
    "schedule_doc_all",
    "schedule_doc_location",
    "schedule_doc_cluster",
    "schedule_doc_climate",
    "schedule_doc_light",
    "schedule_doc_room_schedule",
    "schedule_doc_room_light_schedule",
    "schedule_state_legacy",
    "schedule_state_short",
    # automation / failsafe
    "failsafe_active",
    # ttl constants
    "CAN_SENSOR_CURRENT_TTL_SEC",
    "SOIL_SENSOR_CURRENT_TTL_SEC",
    "ONEWIRE_SENSOR_CURRENT_TTL_SEC",
    "SENSOR_LAST_GOOD_TTL_SEC",
    "FAILSAFE_TTL_SEC",
    "SCHEDULE_DOC_TTL_SEC",
    "AUTOMATION_DECISION_TTL_SEC",
    # monitoring publications
    "monitoring_current_publication_key",
    "monitoring_future_publication_key",
]
