from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time as dt_time
import time
from typing import TYPE_CHECKING, Any, cast

from shared.room_mode_policy import validate_room_mode_choice

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Connection, Pool


class ProfileNotFoundError(LookupError):
    """The requested mode or submode identity does not exist."""


class InvalidProfileIdentityError(ValueError):
    """The requested mode/submode pair is not a permitted profile identity."""


@dataclass(frozen=True, slots=True)
class ActiveModeProjection:
    """One set-based active-identity and parameter projection for runtime snapshots."""

    active_modes: dict[tuple[str, str], dict[str, Any]]
    mode_parameters: dict[tuple[str, str], dict[str, Any]]


class RoomModeRepository(BaseRepository):
    """Repository for room mode operations.

    Handles mode_parameters table with photoperiod/light settings only:
    - Time settings (day/night start)
    - Light settings (main/supplemental intensity, ramp durations)
    """

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)
        # Cache for mode_id and submode_id lookups with 60-second TTL
        self._mode_id_cache: dict[str, tuple[int, float]] = {}
        self._submode_id_cache: dict[str, tuple[int, float]] = {}

    async def _get_mode_id_cached(self, conn: Any, mode_name: str) -> int | None:
        conn = cast("Connection", conn)
        cache_key = mode_name
        if cache_key in self._mode_id_cache:
            mode_id, ts = self._mode_id_cache[cache_key]
            if time.time() - ts < 60:
                return mode_id

        result = await conn.fetchrow("SELECT id FROM room_modes WHERE name = $1", mode_name)
        if result:
            self._mode_id_cache[cache_key] = (result["id"], time.time())
            return result["id"]
        return None

    async def _get_submode_id_cached(
        self, conn: Any, mode_name: str, submode_name: str
    ) -> int | None:
        conn = cast("Connection", conn)
        cache_key = f"{mode_name}:{submode_name}"
        if cache_key in self._submode_id_cache:
            submode_id, ts = self._submode_id_cache[cache_key]
            if time.time() - ts < 60:
                return submode_id

        result = await conn.fetchrow("SELECT id FROM flower_submodes WHERE name = $1", submode_name)
        if result:
            self._submode_id_cache[cache_key] = (result["id"], time.time())
            return result["id"]
        return None

    async def get_room_modes(self) -> list[dict[str, Any]]:
        """Get all available room modes."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                rows = await conn.fetch("SELECT * FROM room_modes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get room modes: {e}")
            return []

    async def get_mode_ids_for_room_cluster(self, location: str, cluster: str) -> list[int]:
        """Get mode IDs with photoperiod parameters for one room and cluster."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                rows = await conn.fetch(
                    """SELECT DISTINCT mode_id FROM mode_parameters
                       WHERE location = $1 AND cluster = $2 ORDER BY mode_id""",
                    location,
                    cluster,
                )
                return [row["mode_id"] for row in rows]
        except Exception as e:
            logger.error(f"Failed to get mode IDs for {location}/{cluster}: {e}")
            return []

    async def get_room_mode_by_name(self, name: str) -> dict[str, Any] | None:
        """Get a single room mode by name (case-insensitive)."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                row = await conn.fetchrow(
                    "SELECT * FROM room_modes WHERE LOWER(name) = LOWER($1) LIMIT 1",
                    name,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get room mode by name '{name}': {e}")
            return None

    async def get_mode_by_name(self, name: str) -> dict[str, Any] | None:
        """Get a single room mode by exact name.

        Query: SELECT * FROM room_modes WHERE name = $1 LIMIT 1
        """
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                row = await conn.fetchrow(
                    "SELECT * FROM room_modes WHERE name = $1 LIMIT 1",
                    name,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get mode by name '{name}': {e}")
            return None

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        """Get flower submodes."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                rows = await conn.fetch("SELECT * FROM flower_submodes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get flower submodes: {e}")
            return []

    async def get_active_mode(
        self, location: str, cluster: str, conn: Connection | None = None
    ) -> dict[str, Any] | None:
        """Read active identity; caller-owned connection failures propagate."""
        query = """SELECT arm.location, arm.cluster, rm.name as mode_name,
                          fs.name as submode_name, arm.mode_id, arm.submode_id
                   FROM room_active_mode arm
                   JOIN room_modes rm ON rm.id = arm.mode_id
                   LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
                   WHERE arm.location = $1 AND arm.cluster = $2"""
        if conn is not None:
            row = await conn.fetchrow(query, location, cluster)
            return dict(row) if row is not None else None
        try:
            async with self.pool.acquire() as connection:
                row = await connection.fetchrow(query, location, cluster)
                return dict(row) if row is not None else None
        except Exception as error:
            logger.error("Failed to get active mode: %s", error)
            return None

    async def set_active_mode(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None = None,
        conn: Connection | None = None,
    ) -> bool:
        """Set a permitted named identity; caller-owned failures propagate."""
        validate_room_mode_choice(location, mode_name, submode_name)
        if conn is None:
            try:
                async with self.pool.acquire() as connection:
                    return await self.set_active_mode(
                        location, cluster, mode_name, submode_name, conn=connection
                    )
            except Exception as error:
                logger.error("Failed to set active mode: %s", error)
                return False
        mode_id = await self._get_mode_id_cached(conn, mode_name)
        if mode_id is None:
            return False
        submode_id = (
            await self._get_submode_id_cached(conn, mode_name, submode_name)
            if submode_name
            else None
        )
        if submode_name and submode_id is None:
            return False
        await self.set_mode_on_connection(conn, location, cluster, mode_id, submode_id)
        return True

    async def get_profile_identity_on_connection(
        self,
        conn: Connection,
        location: str,
        mode_id: int,
        submode_id: int | None,
    ) -> dict[str, Any]:
        """Resolve one exact saved profile identity on a caller-owned connection.

        Performs read-only metadata SELECTs only; no mutation happens here.
        Raises :class:`ProfileNotFoundError` for unknown IDs and
        :class:`InvalidProfileIdentityError` for a non-Flower mode with a
        flower submode or a room-policy violation.
        """
        mode_row = await conn.fetchrow(
            "SELECT id, name, photoperiod_hours, is_constant FROM room_modes WHERE id = $1",
            mode_id,
        )
        if mode_row is None:
            raise ProfileNotFoundError(f"mode {mode_id} does not exist")
        submode_name: str | None = None
        if submode_id is not None:
            submode_row = await conn.fetchrow(
                "SELECT id, name FROM flower_submodes WHERE id = $1", submode_id
            )
            if submode_row is None:
                raise ProfileNotFoundError(f"submode {submode_id} does not exist")
            if str(mode_row["name"]) != "flower":
                raise InvalidProfileIdentityError(
                    f"flower submode '{submode_row['name']}' requires the flower mode, "
                    f"not '{mode_row['name']}'"
                )
            submode_name = str(submode_row["name"])
        mode_name = str(mode_row["name"])
        try:
            validate_room_mode_choice(location, mode_name, submode_name)
        except ValueError as error:
            raise InvalidProfileIdentityError(str(error)) from error
        return {
            "mode_id": int(mode_row["id"]),
            "submode_id": submode_id,
            "mode_name": mode_name,
            "submode_name": submode_name,
            "is_constant": bool(mode_row["is_constant"] or False),
            "photoperiod_hours": mode_row["photoperiod_hours"],
        }

    async def get_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None = None,
        conn: Connection | None = None,
    ) -> dict[str, Any] | None:
        """Get mode parameters from mode_parameters table.

        Returns photoperiod/light settings only (6 operational fields):
        day_start_time, night_start_time, light_ramp_up_minutes, light_ramp_down_minutes,
        main_light_intensity, supplemental_light_intensity.

        ``conn`` joins the caller's transaction; its failures propagate instead
        of becoming a soft None.
        """
        if conn is not None:
            return await self._get_mode_parameters_on_connection(
                conn, location, cluster, mode_name, submode_name
            )
        try:
            async with self.pool.acquire() as connection:
                return await self._get_mode_parameters_on_connection(
                    connection, location, cluster, mode_name, submode_name
                )
        except Exception as error:
            logger.error("Failed to get mode parameters: %s", error)
            return None

    async def _get_mode_parameters_on_connection(
        self,
        conn: Connection,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
    ) -> dict[str, Any] | None:
        mode_id = await self._get_mode_id_cached(conn, mode_name)
        if not mode_id:
            return None

        submode_id: int | None = None
        if submode_name:
            submode_id = await self._get_submode_id_cached(conn, mode_name, submode_name)

        if submode_id:
            row = await conn.fetchrow(
                """
                SELECT * FROM mode_parameters
                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id = $4
            """,
                location,
                cluster,
                mode_id,
                submode_id,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT * FROM mode_parameters
                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id IS NULL
            """,
                location,
                cluster,
                mode_id,
            )

        if row:
            result = dict(row)
            # Format time fields as HH:MM strings
            result["day_start_time"] = (
                str(result["day_start_time"])[:5] if result.get("day_start_time") else "06:00"
            )
            result["night_start_time"] = (
                str(result["night_start_time"])[:5] if result.get("night_start_time") else "18:00"
            )
            return result
        return None

    async def save_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, Any],
        conn: Connection | None = None,
    ) -> bool:
        """Save mode parameters to mode_parameters table.

        Handles photoperiod/light settings only (6 operational columns):
        - Time settings: day_start_time, night_start_time
        - Light ramps: light_ramp_up_minutes, light_ramp_down_minutes
        - Light settings: main/supplemental intensity

        ``conn`` joins the caller's transaction instead of a new acquire.
        """
        if conn is not None:
            return await self._save_mode_parameters_on_connection(
                conn, location, cluster, mode_name, submode_name, params
            )
        try:
            async with self.pool.acquire() as connection:
                return await self._save_mode_parameters_on_connection(
                    connection, location, cluster, mode_name, submode_name, params
                )
        except Exception as error:
            logger.error("Failed to save mode parameters: %s", error)
            return False

    async def _save_mode_parameters_on_connection(
        self,
        conn: Connection,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, Any],
    ) -> bool:
        mode_id = await self._get_mode_id_cached(conn, mode_name)
        if not mode_id:
            logger.error(f"Mode '{mode_name}' not found")
            return False

        submode_id: int | None = None
        if submode_name:
            submode_id = await self._get_submode_id_cached(conn, mode_name, submode_name)

        # Parse time strings to time objects
        day_start = params.get("day_start_time", "06:00")
        night_start = params.get("night_start_time", "18:00")
        if isinstance(day_start, str):
            parts = day_start.split(":")
            day_start = dt_time(int(parts[0]), int(parts[1]))
        if isinstance(night_start, str):
            parts = night_start.split(":")
            night_start = dt_time(int(parts[0]), int(parts[1]))

        # Check if record exists
        existing = await conn.fetchval(
            """
            SELECT id FROM mode_parameters
            WHERE location = $1 AND cluster = $2 AND mode_id = $3
            AND COALESCE(submode_id, -1) = COALESCE($4, -1)
        """,
            location,
            cluster,
            mode_id,
            submode_id,
        )

        if existing:
            # UPDATE existing record
            await conn.execute(
                """
                UPDATE mode_parameters SET
                    day_start_time = $1, night_start_time = $2,
                    light_ramp_up_minutes = $3, light_ramp_down_minutes = $4,
                    main_light_intensity = $5, supplemental_light_intensity = $6,
                    updated_at = NOW()
                WHERE id = $7
            """,
                day_start,
                night_start,
                params.get("light_ramp_up_minutes", 15),
                params.get("light_ramp_down_minutes", 15),
                params.get("main_light_intensity", 100),
                params.get("supplemental_light_intensity", 0),
                existing,
            )
        else:
            # INSERT new record
            await conn.execute(
                """
                INSERT INTO mode_parameters (
                    location, cluster, mode_id, submode_id,
                    day_start_time, night_start_time,
                    light_ramp_up_minutes, light_ramp_down_minutes,
                    main_light_intensity, supplemental_light_intensity,
                    updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            """,
                location,
                cluster,
                mode_id,
                submode_id,
                day_start,
                night_start,
                params.get("light_ramp_up_minutes", 15),
                params.get("light_ramp_down_minutes", 15),
                params.get("main_light_intensity", 100),
                params.get("supplemental_light_intensity", 0),
            )
        return True

    async def set_mode_on_connection(
        self,
        conn: Connection,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
    ) -> dict[str, Any]:
        """Validate and write an exact active identity on the owner transaction."""
        identity = await self.get_profile_identity_on_connection(
            conn, location, mode_id, submode_id
        )
        await conn.execute(
            """INSERT INTO room_active_mode (location, cluster, mode_id, submode_id, activated_at)
               VALUES ($1, $2, $3, $4, NOW())
               ON CONFLICT (location, cluster)
               DO UPDATE SET mode_id = $3, submode_id = $4, activated_at = NOW()""",
            location,
            cluster,
            mode_id,
            submode_id,
        )
        return {"location": location, "cluster": cluster, **identity}

    async def get_active_mode_projection(
        self,
        room_clusters: Sequence[tuple[str, str]],
        conn: Connection | None = None,
    ) -> ActiveModeProjection:
        """One set-based active identity + parameter projection for many rooms.

        Joins room_active_mode to room_modes, LEFT JOINs flower_submodes and
        the exact NULL-safe parameters row, restricted to the runtime
        hierarchy pairs. Active-mode entries always carry mode_id/submode_id
        and names even when parameters are absent; parameter entries keep the
        existing normalized clocks/ramps and are omitted when no row exists.
        An empty hierarchy returns two empty maps without SQL.
        """
        if not room_clusters:
            return ActiveModeProjection(active_modes={}, mode_parameters={})
        pairs = [(str(location), str(cluster)) for location, cluster in room_clusters]
        values_rows = ", ".join(
            f"(${2 * index + 1}::text, ${2 * index + 2}::text, {index})"
            for index in range(len(pairs))
        )
        query = f"""
            WITH requested(loc, clu, ord) AS (
                VALUES {values_rows}
            )
            SELECT arm.location, arm.cluster, arm.mode_id, arm.submode_id,
                   rm.name AS mode_name, fs.name AS submode_name,
                   mp.day_start_time, mp.night_start_time,
                   mp.light_ramp_up_minutes, mp.light_ramp_down_minutes,
                   req.ord
            FROM requested req
            LEFT JOIN room_active_mode arm
              ON arm.location = req.loc AND arm.cluster = req.clu
            LEFT JOIN room_modes rm ON rm.id = arm.mode_id
            LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
            LEFT JOIN mode_parameters mp
              ON mp.location = req.loc AND mp.cluster = req.clu
             AND mp.mode_id = arm.mode_id
             AND mp.submode_id IS NOT DISTINCT FROM arm.submode_id
            ORDER BY req.ord
        """
        args: list[Any] = [value for pair in pairs for value in pair]

        async def _project(c: Connection) -> ActiveModeProjection:
            rows = await c.fetch(query, *args)
            active_modes: dict[tuple[str, str], dict[str, Any]] = {}
            mode_parameters: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                room_key = (str(row["location"]), str(row["cluster"]))
                if row["mode_id"] is not None:
                    active_modes[room_key] = {
                        "mode_id": row["mode_id"],
                        "submode_id": row["submode_id"],
                        "mode_name": row["mode_name"],
                        "submode_name": row["submode_name"],
                    }
                if row["day_start_time"] is not None:
                    mode_parameters[room_key] = {
                        "mode_id": row["mode_id"],
                        "day_start": str(row["day_start_time"])[:5],
                        "night_start": str(row["night_start_time"])[:5],
                        "ramp_up": row["light_ramp_up_minutes"],
                        "ramp_down": row["light_ramp_down_minutes"],
                    }
            return ActiveModeProjection(active_modes=active_modes, mode_parameters=mode_parameters)

        if conn is not None:
            return await _project(conn)
        async with self.pool.acquire() as connection:
            return await _project(connection)
