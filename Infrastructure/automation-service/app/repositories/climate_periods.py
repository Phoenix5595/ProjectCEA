from __future__ import annotations

from datetime import datetime
from datetime import time as datetime_time
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


def _parse_time(t: str) -> datetime_time:
    """Parse 'HH:MM' string to time object for PostgreSQL TIME column."""
    return datetime.strptime(t, "%H:%M").time()


class ClimatePeriodRepository(BaseRepository):
    """Repository for climate period operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_periods(
        self, location: str, cluster: str, mode_id: int | None = None, submode_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Get all climate periods for a location/cluster."""
        query = """
            SELECT id, location, cluster, mode_id, submode_id, period_name,
                   start_time, end_time, ramp_minutes,
                   heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint,
                   details, created_at, updated_at
            FROM climate_periods
            WHERE location = $1 AND cluster = $2
        """
        params: list[Any] = [location, cluster]

        if mode_id is not None:
            query += " AND mode_id = $3"
            params.append(mode_id)
            if submode_id is not None:
                query += " AND submode_id = $4"
                params.append(submode_id)

        query += " ORDER BY start_time"

        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get climate periods: {e}")
            return []

    async def get_periods_for_room_mode(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> list[dict[str, Any]]:
        """Periods for one (mode_id, submode_id) slice, NULL-safe on submode (veg / legacy)."""
        query = """
            SELECT id, location, cluster, mode_id, submode_id, period_name,
                   start_time, end_time, ramp_minutes,
                   heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint,
                   details, created_at, updated_at
            FROM climate_periods
            WHERE location = $1 AND cluster = $2
              AND mode_id = $3
              AND submode_id IS NOT DISTINCT FROM $4
            ORDER BY start_time
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, location, cluster, mode_id, submode_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get climate periods for room mode: {e}")
            return []

    async def save_period(
        self,
        location: str,
        cluster: str,
        period_name: str,
        start_time: str,
        end_time: str,
        ramp_minutes: int = 0,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        vpd_setpoint: float | None = None,
        co2_setpoint: int | None = None,
        details: str | None = None,
        mode_id: int | None = None,
        submode_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Save (insert or update) a climate period."""
        query = """
            INSERT INTO climate_periods (
                location, cluster, mode_id, submode_id, period_name,
                start_time, end_time, ramp_minutes,
                heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint,
                details, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
            ON CONFLICT (location, cluster, mode_id, submode_id, period_name)
            DO UPDATE SET
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                ramp_minutes = EXCLUDED.ramp_minutes,
                heating_setpoint = EXCLUDED.heating_setpoint,
                cooling_setpoint = EXCLUDED.cooling_setpoint,
                vpd_setpoint = EXCLUDED.vpd_setpoint,
                co2_setpoint = EXCLUDED.co2_setpoint,
                details = EXCLUDED.details,
                updated_at = NOW()
            RETURNING id, location, cluster, period_name, start_time, end_time
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    query,
                    location,
                    cluster,
                    mode_id,
                    submode_id,
                    period_name,
                    _parse_time(start_time),
                    _parse_time(end_time),
                    ramp_minutes,
                    heating_setpoint,
                    cooling_setpoint,
                    vpd_setpoint,
                    co2_setpoint,
                    details,
                )
                logger.info(
                    f"save_period result: row={'exists' if row else 'None'}, location={location}, cluster={cluster}, period={period_name}"
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to save climate period: {e}")
            return None

    async def delete_period(self, period_id: int) -> bool:
        """Delete a climate period by ID."""
        query = "DELETE FROM climate_periods WHERE id = $1"
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, period_id)
                return True
        except Exception as e:
            logger.error(f"Failed to delete climate period: {e}")
            return False

    async def delete_periods(
        self, location: str, cluster: str, mode_id: int | None = None, submode_id: int | None = None
    ) -> bool:
        """Delete all periods for a location/cluster."""
        query = "DELETE FROM climate_periods WHERE location = $1 AND cluster = $2"
        params: list[Any] = [location, cluster]

        if mode_id is not None:
            query += " AND mode_id = $3"
            params.append(mode_id)
            if submode_id is not None:
                query += " AND submode_id = $4"
                params.append(submode_id)

        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, *params)
                return True
        except Exception as e:
            logger.error(f"Failed to delete climate periods: {e}")
            return False

    async def get_active_period(
        self, location: str, cluster: str, reference_time: str
    ) -> dict[str, Any] | None:
        """Get the active period at a given time (HH:MM format)."""
        query = """
            SELECT id, location, cluster, period_name,
                   start_time, end_time, ramp_minutes,
                   heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint,
                   details
            FROM climate_periods
            WHERE location = $1 AND cluster = $2
            ORDER BY start_time
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, location, cluster)
                if not rows:
                    return None

                ref_parts = reference_time.split(":")
                ref_mins = int(ref_parts[0]) * 60 + int(ref_parts[1])

                for row in rows:
                    start_parts = str(row["start_time"]).split(":")
                    end_parts = str(row["end_time"]).split(":")
                    start_mins = int(start_parts[0]) * 60 + int(start_parts[1])
                    end_mins = int(end_parts[0]) * 60 + int(end_parts[1])

                    if start_mins <= end_mins:
                        if start_mins <= ref_mins < end_mins:
                            return dict(row)
                    else:
                        if ref_mins >= start_mins or ref_mins < end_mins:
                            return dict(row)

                return None
        except Exception as e:
            logger.error(f"Failed to get active period: {e}")
            return None

    def validate_24h_coverage(self, periods: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        """Validate that periods cover exactly 24h with no overlaps."""
        errors: list[str] = []

        if not periods:
            return False, ["No periods defined"]

        if len(periods) > 7:
            return False, [f"Maximum 7 periods allowed, got {len(periods)}"]

        period_times: list[tuple[int, int, str]] = []

        for p in periods:
            name = p.get("period_name", "Unnamed")
            start = p.get("start_time", "")
            end = p.get("end_time", "")

            if not start or not end:
                errors.append(f"Missing start or end time in period '{name}'")
                continue

            try:
                start_parts = start.split(":")
                end_parts = end.split(":")
                start_mins = int(start_parts[0]) * 60 + int(start_parts[1])
                end_mins = int(end_parts[0]) * 60 + int(end_parts[1])
            except (ValueError, IndexError):
                errors.append(f"Invalid time format in period '{name}': {start} - {end}")
                continue

            ramp = p.get("ramp_minutes", 0) or 0
            if ramp > 0:
                duration = (
                    end_mins - start_mins
                    if end_mins > start_mins
                    else (1440 - start_mins + end_mins)
                )
                if ramp > duration:
                    errors.append(
                        f"ramp_minutes ({ramp}) exceeds period duration ({duration} min) in period '{name}'"
                    )

            period_times.append((start_mins, end_mins, name))

        if errors:
            return False, errors

        period_times.sort(key=lambda x: x[0])

        def overlaps(s1: int, e1: int, s2: int, e2: int) -> bool:
            """Check if [s1,e1) and [s2,e2) overlap. Handles wrapping intervals."""
            if e1 > s1 and e2 > s2:
                return s1 < e2 and s2 < e1
            elif e1 > s1:
                return s2 < e1 or e2 > s1
            elif e2 > s2:
                return s1 < e2 or e1 > s2
            else:
                return True

        for i, (start, end, name) in enumerate(period_times):
            for j in range(i + 1, len(period_times)):
                other_start, other_end, other_name = period_times[j]
                if overlaps(start, end, other_start, other_end):
                    errors.append(
                        f"Overlap: '{name}' ({start}-{end}) and '{other_name}' ({other_start}-{other_end})"
                    )
                    break
            if errors:
                break

        return len(errors) == 0, errors
