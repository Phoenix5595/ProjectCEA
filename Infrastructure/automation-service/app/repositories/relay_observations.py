"""Append-only database writer for physically observed relay facts."""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

from app.control.relay_observation_recorder import RelayObservation


class RelayObservationRepository:
    """Persist sample-time relay observations without updating existing history."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, rows: Sequence[RelayObservation]) -> None:
        """Insert one ordered batch using bound asyncpg parameters."""
        if not rows:
            return
        async with (
            self._pool.acquire() as connection,
            connection.transaction(),
        ):
            await connection.executemany(
                """
                    INSERT INTO relay_observation (
                        observed_at, session_id, channel, observed_state, device_id,
                        device_name, device_type, location, cluster, registry_version, reason
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                [
                    (
                        row.observed_at,
                        row.session_id,
                        row.channel,
                        row.observed_state,
                        row.device_id,
                        row.device_name,
                        row.device_type,
                        row.location,
                        row.cluster,
                        row.registry_version,
                        row.reason,
                    )
                    for row in rows
                ],
            )
