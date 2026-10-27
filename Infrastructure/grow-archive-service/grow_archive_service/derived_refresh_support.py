"""Shared derived-history catalog: known aggregate hierarchy, relay identifiers, the dependency record and advisory-lock helpers."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import asyncpg

from .models import ArchiveError


NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")
HOUR: Final = timedelta(hours=1)
DAY: Final = timedelta(days=1)
RELAY: Final = "relay_observation"
RELAY_HOURLY: Final = "monitoring_relay_duration_1hour"
RELAY_LOCK: Final = "projectcea.relay-overview-rebuild"
# name: (original source stream, immediate parent relation, fixed bucket width)
KNOWN: Final = {
    "monitoring_measurement_1min": ("measurement", "measurement", timedelta(minutes=1)),
    "monitoring_measurement_5min": (
        "measurement",
        "monitoring_measurement_1min",
        timedelta(minutes=5),
    ),
    "monitoring_effective_setpoints_1min": (
        "effective_setpoints",
        "effective_setpoints",
        timedelta(minutes=1),
    ),
    "monitoring_effective_setpoints_5min": (
        "effective_setpoints",
        "effective_setpoints",
        timedelta(minutes=5),
    ),
    "monitoring_automation_state_1min": (
        "automation_state",
        "automation_state",
        timedelta(minutes=1),
    ),
    "monitoring_automation_state_5min": (
        "automation_state",
        "automation_state",
        timedelta(minutes=5),
    ),
    "monitoring_measurement_1hour": (
        "measurement",
        "monitoring_measurement_1min",
        HOUR,
    ),
    "monitoring_measurement_1day": ("measurement", "monitoring_measurement_1hour", DAY),
    "monitoring_light_intensity_1hour": ("effective_setpoints", "effective_setpoints", HOUR),
    "monitoring_light_intensity_1day": (
        "effective_setpoints",
        "monitoring_light_intensity_1hour",
        DAY,
    ),
    "monitoring_requested_pid_1hour": ("automation_state", "automation_state", HOUR),
    "monitoring_requested_pid_1day": ("automation_state", "monitoring_requested_pid_1hour", DAY),
    "monitoring_relay_duration_1day": (RELAY, RELAY_HOURLY, DAY),
}



@dataclass(frozen=True, slots=True)
class AggregateDependency:
    source_table: str
    view_schema: str
    view_name: str
    depth: int
    bucket_width: timedelta


def _sources(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or any(
        not isinstance(v, str) or not NAME.fullmatch(v) for v in values
    ):
        raise ArchiveError("archive_derived_dependency_unknown")
    return tuple(sorted(set(values)))


async def _try_lock(conn: asyncpg.Connection, key: str) -> bool:
    return bool(
        await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1::text,0))", key)
    )


async def _unlock(conn: asyncpg.Connection, key: str) -> None:
    await conn.fetchval("SELECT pg_advisory_unlock(hashtextextended($1::text,0))", key)
