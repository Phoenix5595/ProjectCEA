"""Device repository package.

Provides the DeviceRepository class for device state, mapping, and registry
operations. The class is composed from focused mixins defined in sibling
modules to keep each file under 400 LOC.
"""

from __future__ import annotations

from typing import Any

from ..base import BaseRepository, logger
from .hierarchy import iter_devices_flat
from .projection import (
    DeviceHierarchy,
    RegistryDevice,
    RegistryProjection,
    project_registry_rows,
)
from .registry import RegistryMixin
from .registry_lights import LightRegistryMixin


class DeviceRepository(BaseRepository, RegistryMixin, LightRegistryMixin):
    """Repository for device state and mapping operations."""

    def __init__(self, pool: Any | None = None) -> None:
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

    async def get_device_states(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Get device states for a location and cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_name, channel, state, mode, updated_at
                       FROM device_states
                       WHERE location = $1 AND cluster = $2""",
                    location,
                    cluster,
                )
                return {row["device_name"]: dict(row) for row in rows}
        except Exception as e:
            logger.error(f"Failed to get device states: {e}")
            return {}

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

    async def load_registry_rows(self, connection: Any | None = None) -> list[dict[str, Any]]:
        """Load device-registry rows once without converting query failures into emptiness."""
        query = """SELECT device_id, location, cluster, device_name, display_name, device_type,
                           channel, dimming_enabled, dimming_type, dimming_board_id,
                           dimming_channel, safety_level, pid_enabled, interlock_with,
                           pid_setpoints, per_room_index, created_at, updated_at,
                           (SELECT COUNT(*) FROM schedules s
                            WHERE s.location = device_registry.location
                              AND s.cluster = device_registry.cluster
                              AND s.device_name = device_registry.device_name)
                              AS inherited_schedule_count,
                           COALESCE((SELECT jsonb_agg(s.name ORDER BY s.start_time, s.id)
                                     FROM schedules s
                                     WHERE s.location = device_registry.location
                                       AND s.cluster = device_registry.cluster
                                       AND s.device_name = device_registry.device_name), '[]'::jsonb)
                              AS inherited_schedule_summary
                    FROM device_registry
                    ORDER BY location, cluster, device_name"""
        if connection is not None:
            rows = await connection.fetch(query)
            return [dict(row) for row in rows]

        pool = self.pool
        if pool is None:
            raise RuntimeError("Device repository pool is not initialized")
        async with pool.acquire() as pool_connection:
            rows = await pool_connection.fetch(query)
        return [dict(row) for row in rows]

    async def get_registry_projection(self, connection: Any | None = None) -> RegistryProjection:
        """Return the sole strict projection used by both API and runtime reads."""
        rows = await self.load_registry_rows(connection)
        return project_registry_rows(rows)

    async def get_all_as_hierarchy(self) -> DeviceHierarchy:
        """Return the legacy hierarchy derived only from accepted typed rows."""
        return (await self.get_registry_projection()).hierarchy

    async def get_all_devices_flat(self) -> list[RegistryDevice]:
        """Return the typed API projection derived from the same accepted rows."""
        return list((await self.get_registry_projection()).flat)


__all__ = [
    "DeviceRepository",
    "iter_devices_flat",
]
