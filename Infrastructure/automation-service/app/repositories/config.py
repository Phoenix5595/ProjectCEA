from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from shared.infra_logging import get_logger

from .base import BaseRepository

if TYPE_CHECKING:
    pass


class ConfigConnection(Protocol):
    """The asyncpg surface required to log one configuration version row."""

    async def fetchrow(self, query: str, *args: Any) -> Any: ...


logger = get_logger(__name__)


class ConfigRepository(BaseRepository):
    """Repository for configuration version logging."""

    async def log_config_version(
        self,
        config_type: str,
        author: str | None = None,
        comment: str | None = None,
        location: str | None = None,
        cluster: str | None = None,
        changes: dict[str, Any] | None = None,
        conn: ConfigConnection | None = None,
    ) -> int | None:
        """Log a configuration change to config_versions table.

        Args:
            config_type: Type of config change ('setpoint', 'schedule', 'pid', 'safety')
            author: Author of the change (optional)
            comment: Comment describing the change (optional)
            location: Location name if applicable (optional)
            cluster: Cluster name if applicable (optional)
            changes: Dictionary of changes made (optional)
            conn: Caller-owned transaction connection for the shared
                schedule/activation unit; a supplied-connection failure
                propagates instead of becoming a soft None.

        Returns:
            version_id if successful, None otherwise
        """

        async def _log(c: ConfigConnection) -> int | None:
            row = await c.fetchrow(
                """
                INSERT INTO config_versions
                (timestamp, author, comment, config_type, location, cluster, changes)
                VALUES (NOW(), $1, $2, $3, $4, $5, $6)
                RETURNING version_id
            """,
                author,
                comment,
                config_type,
                location,
                cluster,
                json.dumps(changes) if changes else None,
            )
            return row["version_id"] if row else None

        if conn is not None:
            return await _log(conn)
        try:
            async with self.pool.acquire() as conn:
                return await _log(conn)
        except Exception as e:
            logger.error(f"Error logging config version: {e}")
            return None

    async def get_latest_config_version(self) -> int | None:
        """Return the newest configuration version cursor, or None when never logged."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT MAX(version_id) AS version_id FROM config_versions"
                )
        except Exception as e:
            logger.error(f"Error reading latest config version: {e}")
            return None
        return row["version_id"] if row else None
