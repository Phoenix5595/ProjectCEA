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
   - ``sensor:*``     — current sensor values (state, not history).
   - ``cea:sensor:*`` — legacy fully-qualified form (see LEGACY NOTES).
   - ``schedules:*``  — schedule JSON docs by location/cluster.
   - ``cea:schedule:*`` — legacy + schedule state (see LEGACY NOTES).
   - ``automation:*`` — last-issued automation decision per device.
   - ``failsafe:*``   — per-cluster failsafe-active flag.
   - ``sensor:raw``   — main XADD telemetry stream (CAN + soil + 1-wire).
   - ``stream:control`` — XADD stream of effective setpoint decisions.

3. **Retention is a property of the stream, not the writer.**
   ``SENSOR_RAW_MAXLEN`` and ``CONTROL_STREAM_MAXLEN`` are the only numbers
   that should appear as ``maxlen=`` arguments. Writers pass
   ``approximate=True`` so Redis can trim in buckets of ~100 entries, which
   is roughly free.

LEGACY NOTES (do not port to new code)
--------------------------------------
Two parallel sensor schemes exist in production today:

- short form    : ``sensor:{name}`` / ``sensor:{name}:ts``
                  (current-value + write-timestamp, used by
                  ``backend/app/redis_stream_reader.py`` and onewire worker)
- full form     : ``cea:sensor:{location}:{cluster}:{sensor_type}`` (+ ``_ts``)
                  (keyed by the topology tuple, used by automation's setpoint
                  reader)

Both are populated by the stream consumer(s); the ``cea:sensor:`` form is
the one that is actually joined against ``schedules:*``/``automation:*`` in
control-loop decisions. A future dual-write → dual-read → retire pass will
collapse the short form into the full form. Until then, BOTH forms are
canonical for THEIR existing readers.

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

SENSOR_RAW_MAXLEN: Final[int] = 100_000
"""Retention for the sensor:raw stream. At the observed ~3 msg/s CAN rate
this is ~9 hours of buffer — enough for any stream-reader to reconnect and
catch up after a transient outage. Historical durability is owned by
TimescaleDB, not by this stream."""

CONTROL_STREAM_MAXLEN: Final[int] = 100_000
"""Same retention envelope as SENSOR_RAW_MAXLEN for consistency.
Control-loop ticks are ~0.3 Hz so this is weeks of buffer; trimming keeps
memory bounded if a dashboard ever subscribes and falls behind."""

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
    """``sensor:{name}`` — single current value.

    Used by the stream-reader path that stores raw per-sensor floats. Paired
    with ``sensor_short_ts()`` for the write-timestamp companion key.
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


def sensor_last_good(location: str, cluster: str, sensor_name: str, *, legacy: bool = False) -> str:
    """Last-known-good cached value, used during short sensor outages so the
    control loop doesn't immediately enter failsafe.

    Two forms exist:

    - canonical : ``cea:sensor:{location}:{cluster}:{sensor_name}_last_good``
    - legacy    : ``sensor:{cluster}:{sensor_name}:last_good`` (pre-phase-5e
      form, still read by the older stream-reader path)

    Default returns the canonical form; pass ``legacy=True`` when touching
    the pre-phase-5e path.
    """
    if legacy:
        return f"sensor:{cluster}:{sensor_name}:last_good"
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


def schedule_state_infix(location: str, cluster: str) -> str:
    """``cea:schedule:state:{location}:{cluster}`` — legacy infix form.
    Same data, different callers. Future consolidation will pick one."""
    return f"cea:schedule:state:{location}:{cluster}"


def schedule_state_short(location: str, cluster: str) -> str:
    """``schedule:state:{location}:{cluster}`` — third competing form
    without the ``cea:`` prefix. Present in older automation code paths."""
    return f"schedule:state:{location}:{cluster}"


# ---------------------------------------------------------------------------
# Automation + failsafe keys
# ---------------------------------------------------------------------------


def automation_decision(location: str, cluster: str, device_name: str) -> str:
    """``automation:{location}:{cluster}:{device_name}`` — last-issued
    automation decision for a device. Used by the backend to surface
    "who/what last moved this" in the dashboard."""
    return f"automation:{location}:{cluster}:{device_name}"


def failsafe_active(location: str, cluster: str) -> str:
    """``failsafe:{location}:{cluster}`` — 1 / 0 flag; when set, the
    automation control loop is paused for that cluster and devices fall
    back to their safe-default outputs."""
    return f"failsafe:{location}:{cluster}"


# ---------------------------------------------------------------------------
# TTL policy
# ---------------------------------------------------------------------------

# These are the defaults writers should apply via EXPIRE (or SET ... EX).
# A key without a TTL is implicitly "authoritative, durable across service
# restart" — today only ``schedules:*`` + ``automation:*`` qualify.

SENSOR_VALUE_TTL_SEC: Final[int] = 300
"""5 min. Current-value sensor keys (``sensor:*``, ``cea:sensor:*``) must
be refreshed at least every 5 min or they are treated as stale. At the
expected ~3 Hz sensor cadence this is comfortably within reach; if Redis
loses a key before the sensor writes again, the reader simply falls back
to the last-good form (which has no TTL)."""

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


__all__ = [
    # streams
    "SENSOR_RAW_STREAM",
    "CONTROL_STREAM",
    "SENSOR_RAW_MAXLEN",
    "CONTROL_STREAM_MAXLEN",
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
    "schedule_state_infix",
    "schedule_state_short",
    # automation / failsafe
    "automation_decision",
    "failsafe_active",
    # ttl constants
    "SENSOR_VALUE_TTL_SEC",
    "SENSOR_LAST_GOOD_TTL_SEC",
    "FAILSAFE_TTL_SEC",
    "SCHEDULE_DOC_TTL_SEC",
    "AUTOMATION_DECISION_TTL_SEC",
]
