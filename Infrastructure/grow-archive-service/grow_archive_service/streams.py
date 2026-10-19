"""Stable source-table bindings used by archive lifecycle policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Mapping

from .models import ArchiveError


@dataclass(frozen=True, slots=True)
class StreamBinding:
    name: str
    core: bool
    time_column: str
    room_column: str | None
    sensor_column: str | None
    device_column: str | None
    event_column: str | None


STREAM_BINDINGS: Final[Mapping[str, StreamBinding]] = {
    binding.name: binding
    for binding in (
        StreamBinding("measurement", True, "time", None, "sensor_id", None, None),
        StreamBinding("actuator_events", True, "time", None, None, "device_id", None),
        StreamBinding("automation_state", True, "timestamp", "location", None, None, None),
        StreamBinding("effective_setpoints", True, "timestamp", "location", None, None, None),
        StreamBinding("control_history", True, "timestamp", "location", None, None, None),
        StreamBinding("relay_observation", True, "observed_at", "location", None, None, None),
        StreamBinding("monitoring_room_photoperiod", True, "observed_at", "location", None, None, None),
        StreamBinding("monitoring_photoperiod_coverage", True, "observed_at", "location", None, None, None),
        StreamBinding("light_state_history", False, "triggered_at", "location", None, None, None),
        StreamBinding("mode_transition_history", False, "triggered_at", "location", None, None, None),
        StreamBinding("pid_parameter_history", False, "timestamp", "location", None, None, None),
        StreamBinding("setpoint_history", False, "timestamp", "location", None, None, None),
        StreamBinding("pid_adjustments", False, "created_at", "location", None, None, None),
        StreamBinding("config_versions", False, "timestamp", "location", None, None, None),
        StreamBinding("rollout_events", False, "timestamp", None, None, None, None),
        StreamBinding("calendar_mode_application", False, "applied_at", None, None, None, "event_id"),
    )
}

HYPERTABLES: Final = (
    "measurement",
    "actuator_events",
    "automation_state",
    "effective_setpoints",
    "control_history",
    "relay_observation",
)


async def time_nullable(conn, stream: str, time_column: str) -> bool:
    """Return the live nullability of a catalogued source time column."""
    nullable = await conn.fetchval(
        "SELECT NOT attnotnull FROM pg_attribute "
        "WHERE attrelid=to_regclass('public.'||quote_ident($1)) "
        "AND attname=$2 AND NOT attisdropped",
        stream,
        time_column,
    )
    if nullable is None:
        raise ArchiveError("schema_descriptor_unavailable")
    return bool(nullable)
