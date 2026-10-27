"""Photoperiod history persistence contract: phase/coverage observations, recorder state, the store protocol and its idempotent asyncpg adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, TypeAlias

import asyncpg

from app.schemas.monitoring_models import Phase, RuntimeSnapshotVersion

PHASE_SOURCE: Final = "photoperiod_transition"

CoverageState: TypeAlias = Literal["available", "unavailable"]
CoverageReason: TypeAlias = Literal[
    "initial",
    "started",
    "stopped",
    "control_failure",
    "recording_gap",
    "recovered",
    "unclean_restart",
]

_PHASE_INSERT_SQL: Final = """
INSERT INTO monitoring_room_photoperiod (
    "time", location, cluster, phase, mode_id, submode_id,
    runtime_snapshot_version, source
)
SELECT $1, $2, $3, $4, $5, $6, $7, $8
WHERE NOT EXISTS (
    SELECT 1
    FROM monitoring_room_photoperiod AS prior
    WHERE prior.location = $2
      AND prior.cluster = $3
      AND prior.source = $8
      AND prior."time" = $1
      AND prior.phase = $4
)
  AND (
    SELECT latest.phase
    FROM monitoring_room_photoperiod AS latest
    WHERE latest.location = $2
      AND latest.cluster = $3
      AND latest.source = $8
    ORDER BY latest."time" DESC, latest.id DESC
    LIMIT 1
  ) IS DISTINCT FROM $4
"""

_COVERAGE_INSERT_SQL: Final = """
INSERT INTO monitoring_photoperiod_coverage (
    "time", location, cluster, state, reason, runtime_snapshot_version
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (location, cluster, "time", state) DO NOTHING
"""

_PHASE_STATE_SQL: Final = """
SELECT DISTINCT ON (location, cluster)
    location, cluster, phase, "time", runtime_snapshot_version
FROM monitoring_room_photoperiod
WHERE source = 'photoperiod_transition'
ORDER BY location, cluster, "time" DESC, id DESC
"""

_COVERAGE_STATE_SQL: Final = """
SELECT DISTINCT ON (location, cluster)
    location, cluster, state, "time", runtime_snapshot_version
FROM monitoring_photoperiod_coverage
ORDER BY location, cluster, "time" DESC, id DESC
"""

# The climate row the same per-room control tick writes with the phase offer's
# ``current_time``; rows flush in 5 s batches, so this is at most the real last tick.
_LAST_CONTROL_TICK_SQL: Final = """
SELECT "time"
FROM effective_setpoints
WHERE location = $1
  AND cluster = $2
  AND device_name = 'Main'
  AND "time" > $3
  AND "time" < $4
ORDER BY "time" DESC
LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class PhotoperiodObservation:
    """One exact room phase resolved by the control tick."""

    observed_at: datetime
    location: str
    cluster: str
    phase: Phase
    mode_id: int | None
    submode_id: int | None
    runtime_snapshot_version: RuntimeSnapshotVersion


@dataclass(frozen=True, slots=True)
class PhotoperiodCoverageObservation:
    """One sparse recorder-availability boundary; never a periodic sample."""

    observed_at: datetime
    location: str
    cluster: str
    state: CoverageState
    reason: CoverageReason
    runtime_snapshot_version: RuntimeSnapshotVersion


@dataclass(frozen=True, slots=True)
class PhotoperiodRecorderState:
    """Latest persisted new-source phase and coverage boundary facts for one room."""

    location: str
    cluster: str
    phase: Phase | None
    phase_observed_at: datetime | None
    coverage_state: CoverageState | None
    coverage_observed_at: datetime | None
    runtime_snapshot_version: RuntimeSnapshotVersion
    # Latest control tick of the previous run after its last phase/coverage fact.
    last_control_at: datetime | None = None


PhotoperiodHistoryRow: TypeAlias = PhotoperiodObservation | PhotoperiodCoverageObservation


class PhotoperiodHistoryStore(Protocol):
    """Append-only persistence seam, implemented by the database adapter or a fake."""

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        """Persist one bounded batch in one database transaction."""
        ...

    async def read_state(self) -> tuple[PhotoperiodRecorderState, ...]:
        """Read the latest persisted transition and coverage boundary per room."""
        ...


class DatabasePhotoperiodHistoryStore:
    """Append rows through the initialized automation database pool, idempotently."""

    def __init__(self, pool: asyncpg.Pool, *, started_at: datetime | None = None) -> None:
        self._pool = pool
        # Constructed before the control loop starts, so this run's own ticks are
        # never read as evidence of the previous run.
        self._started_at = started_at if started_at is not None else datetime.now(tz=UTC)

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        """Insert an already-bounded batch in one transaction and never duplicate facts.

        Phase rows are conditional on the latest persisted ``photoperiod_transition``
        phase being absent or different, and on the same room/timestamp/phase row not
        already existing, so restart seeding and retry-after-ambiguous-commit settle
        without duplicates. Coverage rows use the sparse unique key with
        ``ON CONFLICT DO NOTHING``.
        """
        if not rows:
            return
        async with self._pool.acquire() as connection, connection.transaction():
            for row in rows:
                if isinstance(row, PhotoperiodObservation):
                    await connection.execute(
                        _PHASE_INSERT_SQL,
                        row.observed_at,
                        row.location,
                        row.cluster,
                        row.phase.value,
                        row.mode_id,
                        row.submode_id,
                        int(row.runtime_snapshot_version),
                        PHASE_SOURCE,
                    )
                    continue
                await connection.execute(
                    _COVERAGE_INSERT_SQL,
                    row.observed_at,
                    row.location,
                    row.cluster,
                    row.state,
                    row.reason,
                    int(row.runtime_snapshot_version),
                )

    async def read_state(self) -> tuple[PhotoperiodRecorderState, ...]:
        """Read latest-row facts and prior-run tick evidence in one repeatable-read transaction.

        Old ``source='photoperiod'`` rows are never read here: they were samples from
        the incomplete Sleep/Drying-only producer and must not seed new transition
        continuity. For a room whose last coverage fact is ``available`` (the previous
        run may have stopped uncleanly), the latest control tick after its last
        phase/coverage fact and before this process started is read as well.
        """
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            phase_rows = await connection.fetch(_PHASE_STATE_SQL)
            coverage_rows = await connection.fetch(_COVERAGE_STATE_SQL)
            phases = {(row["location"], row["cluster"]): row for row in phase_rows}
            coverage = {(row["location"], row["cluster"]): row for row in coverage_rows}
            last_control: dict[tuple[str, str], datetime | None] = {}
            for room_key, coverage_row in coverage.items():
                if coverage_row["state"] != "available":
                    continue
                lower = coverage_row["time"]
                phase_row = phases.get(room_key)
                if phase_row is not None and phase_row["time"] > lower:
                    lower = phase_row["time"]
                last_control[room_key] = await connection.fetchval(
                    _LAST_CONTROL_TICK_SQL, room_key[0], room_key[1], lower, self._started_at
                )
        states: list[PhotoperiodRecorderState] = []
        for room_key in sorted(set(phases) | set(coverage)):
            phase_row = phases.get(room_key)
            coverage_row = coverage.get(room_key)
            version_row: asyncpg.Record
            if phase_row is not None and coverage_row is not None:
                version_row = (
                    phase_row if phase_row["time"] >= coverage_row["time"] else coverage_row
                )
            else:
                version_row = phase_row if phase_row is not None else coverage_row
            if version_row is None:  # pragma: no cover - both maps are populated
                continue
            states.append(
                PhotoperiodRecorderState(
                    location=room_key[0],
                    cluster=room_key[1],
                    phase=Phase(str(phase_row["phase"])) if phase_row is not None else None,
                    phase_observed_at=phase_row["time"] if phase_row is not None else None,
                    coverage_state=(coverage_row["state"] if coverage_row is not None else None),
                    coverage_observed_at=(
                        coverage_row["time"] if coverage_row is not None else None
                    ),
                    runtime_snapshot_version=RuntimeSnapshotVersion(
                        int(version_row["runtime_snapshot_version"])
                    ),
                    last_control_at=last_control.get(room_key),
                )
            )
        return tuple(states)
