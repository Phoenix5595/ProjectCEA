"""Reviewed non-persistent mutation-route exemptions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class MutationHttpMethod(StrEnum):
    """HTTP methods that require persisted-mutation event coverage."""

    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class NonPersistentEffect(StrEnum):
    """Constrained effects that are safe to exempt from persisted-event coverage."""

    LOCAL_TIMING_RESET = "local_timing_reset"
    LOCAL_CACHE_CLEAR = "local_cache_clear"
    TRANSIENT_HARDWARE_TEST = "transient_hardware_test"
    TRANSIENT_HARDWARE_COMMAND = "transient_hardware_command"
    TRANSIENT_REMOTE_CONNECTION_PROBE = "transient_remote_connection_probe"


@dataclass(frozen=True, slots=True)
class MutationRouteExclusion:
    """One audited endpoint whose effect cannot persist authoritative state."""

    method: MutationHttpMethod
    path: str
    rationale: str
    effect: NonPersistentEffect


MUTATION_ROUTE_EXCLUSIONS: Final[tuple[MutationRouteExclusion, ...]] = (
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/calendar/sync/connections/test",
        rationale="Reads remote CalDAV calendars without persisting authoritative state.",
        effect=NonPersistentEffect.TRANSIENT_REMOTE_CONNECTION_PROBE,
    ),
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/timing/reset",
        rationale="Resets process-local timing instrumentation only.",
        effect=NonPersistentEffect.LOCAL_TIMING_RESET,
    ),
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/flags/cache/clear",
        rationale="Clears only the local feature-flag cache.",
        effect=NonPersistentEffect.LOCAL_CACHE_CLEAR,
    ),
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/hardware/relays/test",
        rationale="Executes a transient hardware diagnostic without repository persistence.",
        effect=NonPersistentEffect.TRANSIENT_HARDWARE_TEST,
    ),
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/lights/{location}/{cluster}/{device_name}/intensity",
        rationale="Direct DFR intensity command has no authoritative persistence seam.",
        effect=NonPersistentEffect.TRANSIENT_HARDWARE_COMMAND,
    ),
    MutationRouteExclusion(
        method=MutationHttpMethod.POST,
        path="/api/lights/{location}/{cluster}/{device_name}/voltage",
        rationale="Direct DFR voltage command has no authoritative persistence seam.",
        effect=NonPersistentEffect.TRANSIENT_HARDWARE_COMMAND,
    ),
)


__all__ = [
    "MUTATION_ROUTE_EXCLUSIONS",
    "MutationHttpMethod",
    "MutationRouteExclusion",
    "NonPersistentEffect",
]
