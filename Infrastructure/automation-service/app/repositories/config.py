from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from shared.infra_logging import get_logger

from .base import BaseRepository

if TYPE_CHECKING:
    pass

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
    ) -> int | None:
        """Log a configuration change to config_versions table.

        Args:
            config_type: Type of config change ('setpoint', 'schedule', 'pid', 'safety')
            author: Author of the change (optional)
            comment: Comment describing the change (optional)
            location: Location name if applicable (optional)
            cluster: Cluster name if applicable (optional)
            changes: Dictionary of changes made (optional)

        Returns:
            version_id if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
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
        except Exception as e:
            logger.error(f"Error logging config version: {e}")
            return None
