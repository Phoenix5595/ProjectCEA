from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


MINIMUM_NORMAL_TARGET_INTENSITY = 10.0
MAXIMUM_TARGET_INTENSITY = 100.0


def validate_normal_target_intensity(target_intensity: float) -> float:
    """Validate a normal photoperiod target intensity."""
    if MINIMUM_NORMAL_TARGET_INTENSITY <= target_intensity <= MAXIMUM_TARGET_INTENSITY:
        return target_intensity
    raise ValueError("Normal light target intensity must be between 10.0 and 100.0")


class LightTargetIntensityRepository(BaseRepository):
    """Repository for per-(device, mode) light target intensity anchors."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_intensity(self, device_id: int, mode_id: int) -> float | None:
        """Get the target intensity for a specific device and mode."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT target_intensity
                       FROM light_target_intensity
                       WHERE device_id = $1 AND mode_id = $2""",
                    device_id,
                    mode_id,
                )
                if row:
                    return float(row["target_intensity"])
        except Exception as e:
            logger.error(f"Failed to get light target intensity for {device_id}/{mode_id}: {e}")
        return None

    async def get_intensities_for_room(
        self, location: str, cluster: str, mode_id: int
    ) -> dict[int, float]:
        """Get target intensities for all lights in a room/cluster for a mode.

        Returns a mapping of device_id -> target_intensity.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT lti.device_id, lti.target_intensity
                       FROM light_target_intensity lti
                       JOIN device_registry dr ON dr.device_id = lti.device_id
                       WHERE dr.location = $1 AND dr.cluster = $2 AND lti.mode_id = $3""",
                    location,
                    cluster,
                    mode_id,
                )
                return {row["device_id"]: float(row["target_intensity"]) for row in rows}
        except Exception as e:
            logger.error(
                f"Failed to get light intensities for {location}/{cluster} mode {mode_id}: {e}"
            )
            return {}

    async def set_intensity(self, device_id: int, mode_id: int, target_intensity: float) -> bool:
        """Set or update the target intensity for a specific device and mode."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO light_target_intensity (device_id, mode_id, target_intensity, updated_at)
                       VALUES ($1, $2, $3, NOW())
                       ON CONFLICT (device_id, mode_id)
                       DO UPDATE SET target_intensity = EXCLUDED.target_intensity,
                                     updated_at = NOW()""",
                    device_id,
                    mode_id,
                    target_intensity,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set light target intensity for {device_id}/{mode_id}: {e}")
            return False

    async def get_all_intensities(self) -> dict[tuple[int, int], float]:
        """Get all target intensities keyed by (device_id, mode_id)."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT device_id, mode_id, target_intensity FROM light_target_intensity"
                )
                return {
                    (row["device_id"], row["mode_id"]): float(row["target_intensity"])
                    for row in rows
                }
        except Exception as e:
            logger.error(f"Failed to get all light target intensities: {e}")
            return {}
