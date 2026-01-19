from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class DeviceRepository(BaseRepository):
    """Repository for device state and mapping operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_device_state(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get current device state."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, device_name, channel, state, mode, updated_at
                       FROM device_states 
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    location,
                    cluster,
                    device_name,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get device state: {e}")
        return None

    async def set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        state: bool,
        mode: str = "auto",
    ) -> bool:
        """Set device state in database."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO device_states (location, cluster, device_name, channel, state, mode, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, NOW())
                       ON CONFLICT (location, cluster, device_name) 
                       DO UPDATE SET channel = $4, state = $5, mode = $6, updated_at = NOW()""",
                    location,
                    cluster,
                    device_name,
                    channel,
                    state,
                    mode,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set device state: {e}")
            return False

    async def get_all_device_states(self) -> list[dict[str, Any]]:
        """Get all device states."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM device_states ORDER BY location, cluster, device_name"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all device states: {e}")
            return []

    async def get_device_mapping(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get device hardware mapping."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, device_name, channel, active_high, safe_state, mcp_board_id
                       FROM device_mappings 
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    location,
                    cluster,
                    device_name,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get device mapping: {e}")
        return None

    async def set_device_mapping(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        active_high: bool = True,
        safe_state: bool = False,
        mcp_board_id: int = 0,
    ) -> bool:
        """Set device hardware mapping."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO device_mappings (location, cluster, device_name, channel, active_high, safe_state, mcp_board_id)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (location, cluster, device_name) 
                       DO UPDATE SET channel = $4, active_high = $5, safe_state = $6, mcp_board_id = $7""",
                    location,
                    cluster,
                    device_name,
                    channel,
                    active_high,
                    safe_state,
                    mcp_board_id,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set device mapping: {e}")
            return False

    async def get_all_device_mappings(self) -> list[dict[str, Any]]:
        """Get all device mappings."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM device_mappings ORDER BY location, cluster, device_name"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all device mappings: {e}")
            return []

    async def get_latest_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> float | None:
        """Get latest light intensity for a device."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT effective_light_intensity FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2 AND device_name = $3
                       ORDER BY timestamp DESC LIMIT 1""",
                    location,
                    cluster,
                    device_name,
                )
                if row and row["effective_light_intensity"] is not None:
                    return float(row["effective_light_intensity"])
        except Exception as e:
            logger.error(f"Failed to get light intensity: {e}")
        return None
