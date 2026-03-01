from __future__ import annotations

from datetime import datetime
import time
from typing import TYPE_CHECKING, Any

from app.state import StateManager, get_state_manager  # type: ignore

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class SetpointRepository(BaseRepository):
    """Repository for setpoint operations."""

    def __init__(self, pool: Pool | None = None, redis_client: Any = None) -> None:
        super().__init__(pool)
        self._redis_client = redis_client
        self._batch_buffer: list[dict[str, Any]] = []
        # Reduced from 10s to 5s for faster Grafana updates (trade-off: more DB writes)
        self._batch_interval = 5.0
        self._last_batch_flush = time.time()

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def flush_batch_buffer(self) -> int:
        """Flush batched effective setpoint logs to database.

        Returns:
            Number of records flushed (0 if no records to flush)
        """
        if not self._batch_buffer:
            return 0

        flushed_count = 0
        batch_data: list[dict[str, Any]] = []
        try:
            # Use a single batch insert for all buffered records
            batch_data = self._batch_buffer.copy()
            self._batch_buffer.clear()

            if batch_data:
                # Insert all records in a single transaction
                async with self.pool.acquire() as conn:
                    # Prepare the insert statement
                    insert_query = """
                            INSERT INTO effective_setpoints (
                                timestamp, location, cluster, device_name, mode,
                                effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
                                effective_co2_setpoint, effective_vpd_setpoint,
                                nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
                                nominal_co2_setpoint, nominal_vpd_setpoint,
                                ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
                                ramp_progress_co2, ramp_progress_vpd,
                                effective_light_intensity, nominal_light_intensity, ramp_progress_light
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)
                        """
                    await conn.executemany(
                        insert_query,
                        [
                            (
                                record["timestamp"],
                                record["location"],
                                record["cluster"],
                                record["device_name"],
                                record.get("mode"),
                                record["effective_heating_setpoint"],
                                record["effective_cooling_setpoint"],
                                record["effective_humidity_setpoint"],
                                record["effective_co2_setpoint"],
                                record["effective_vpd_setpoint"],
                                record["nominal_heating_setpoint"],
                                record["nominal_cooling_setpoint"],
                                record["nominal_humidity_setpoint"],
                                record["nominal_co2_setpoint"],
                                record["nominal_vpd_setpoint"],
                                record["ramp_progress_heating"],
                                record["ramp_progress_cooling"],
                                record["ramp_progress_humidity"],
                                record["ramp_progress_co2"],
                                record["ramp_progress_vpd"],
                                record["effective_light_intensity"],
                                record["nominal_light_intensity"],
                                record["ramp_progress_light"],
                            )
                            for record in batch_data
                        ],
                    )

                    flushed_count = len(batch_data)
                logger.debug(f"Flushed {flushed_count} batched effective setpoint records")

        except Exception as e:
            logger.error(f"Failed to flush batch buffer: {e}", exc_info=True)
            # Re-add failed records to buffer for retry
            self._batch_buffer.extend(batch_data)

        self._last_batch_flush = time.time()
        return flushed_count

    async def get_setpoint(
        self, location: str, cluster: str, mode: str | None = None
    ) -> dict[str, Any] | None:
        """Get setpoint for location/cluster/mode.

        Args:
            location: Location name
            cluster: Cluster name
            mode: Mode name (day/night/pre_day/pre_night). If None, defaults to 'day'.
        """
        db_mode = mode if mode and mode != "main" else "day"

        # 1) Cache-aside: check StateManager first (fast in-memory / Redis cache underneath)
        state: StateManager | None = None
        try:
            state = get_state_manager()
            cached_from_state = await state.get_setpoint(location, cluster)
            if cached_from_state is not None:
                return cached_from_state
        except Exception:
            # If StateManager is unavailable for any reason, fall back to DB cache path
            logger.debug("Setpoint: StateManager unavailable or error during get_setpoint lookup")

        # 2) Fallback to local in-repository cache as a secondary cache (legacy behavior)
        cache_key = self._get_cache_key("get_setpoint", location, cluster, db_mode)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        # 3) DB lookup on cache miss
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, mode, heating_setpoint, cooling_setpoint,
                               humidity, co2, vpd, ramp_in_duration, updated_at
                        FROM setpoints
                        WHERE location = $1 AND cluster = $2 AND mode = $3""",
                    location,
                    cluster,
                    db_mode,
                )
                if row:
                    result = dict(row)
                    self._set_cached_result(cache_key, result)

                    # 4) Populate StateManager cache (cache-aside: populate cache on miss)
                    try:
                        if state is not None:
                            await state.set_setpoint(
                                location,
                                cluster,
                                result.get("heating_setpoint"),
                                result.get("cooling_setpoint"),
                                result.get("humidity"),
                                result.get("co2"),
                            )
                    except Exception:
                        pass

                    return result
        except Exception as e:
            logger.error(f"Failed to get setpoint: {e}")
        return None

    async def set_setpoint(
        self,
        location: str,
        cluster: str,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        vpd: float | None = None,
        mode: str | None = None,
        ramp_in_duration: int | None = None,
        source: str = "api",
        expected_version: datetime | None = None,
    ) -> tuple[bool, datetime | None]:
        """Set setpoint with optimistic locking.

        Args:
            location: Location name
            cluster: Cluster name
            heating_setpoint: Heating setpoint temperature
            cooling_setpoint: Cooling setpoint temperature
            humidity: Humidity setpoint
            co2: CO2 setpoint
            vpd: VPD setpoint
            mode: Mode name. If None or 'main', defaults to 'day'.
            ramp_in_duration: Ramp duration in minutes
            source: Source of update
            expected_version: For optimistic locking

        Returns:
            Tuple of (success, new_updated_at)
        """
        db_mode = mode if mode and mode != "main" else "day"

        try:
            async with self.pool.acquire() as conn:
                if expected_version:
                    row = await conn.fetchrow(
                        "SELECT updated_at FROM setpoints WHERE location = $1 AND cluster = $2 AND mode = $3",
                        location,
                        cluster,
                        db_mode,
                    )
                    if row:
                        current = row["updated_at"]
                        if current and current != expected_version:
                            return (False, None)

                heat = heating_setpoint if heating_setpoint is not None else 20.0
                cool = cooling_setpoint if cooling_setpoint is not None else 25.0
                hum = humidity if humidity is not None else 60.0
                co2_val = co2 if co2 is not None else 800.0
                vpd_val = vpd if vpd is not None else 1.0
                ramp_in = ramp_in_duration if ramp_in_duration is not None else 30

                new_row = await conn.fetchrow(
                    """INSERT INTO setpoints (location, cluster, mode, heating_setpoint, cooling_setpoint,
                                             humidity, co2, vpd, ramp_in_duration, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                       ON CONFLICT (location, cluster, mode)
                       DO UPDATE SET heating_setpoint = $4, cooling_setpoint = $5, humidity = $6,
                                    co2 = $7, vpd = $8, ramp_in_duration = $9, updated_at = NOW()
                       RETURNING updated_at""",
                    location,
                    cluster,
                    db_mode,
                    heat,
                    cool,
                    hum,
                    co2_val,
                    vpd_val,
                    ramp_in,
                )
                self.clear_cache()
                # Invalidate caches in StateManager and local repository to ensure
                # subsequent reads fetch fresh data from the DB or StateManager cache.
                try:
                    # Local repository cache invalidation (already present)
                    self.invalidate_cache_for_location_cluster(location, cluster)
                except Exception:
                    pass

                try:
                    state = get_state_manager()
                    # Invalidate known Redis-backed keys related to this setpoint
                    if state is not None:
                        await state.delete(f"setpoint:{location}:{cluster}:heating_setpoint")
                        await state.delete(f"setpoint:{location}:{cluster}:cooling_setpoint")
                        await state.delete(f"setpoint:{location}:{cluster}:humidity")
                        await state.delete(f"setpoint:{location}:{cluster}:co2")
                        await state.delete(f"setpoint:{location}:{cluster}:source")
                except Exception:
                    pass
                return (True, new_row["updated_at"] if new_row else None)
        except Exception as e:
            logger.error(f"Failed to set setpoint: {e}")
            return (False, None)

    async def get_all_setpoints_for_location_cluster(
        self, location: str, cluster: str
    ) -> list[dict[str, Any]]:
        """Get all setpoints for a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, mode, heating_setpoint, cooling_setpoint,
                              humidity, co2, vpd, ramp_in_duration, updated_at
                       FROM setpoints WHERE location = $1 AND cluster = $2""",
                    location,
                    cluster,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get setpoints: {e}")
            return []

    async def get_latest_effective_setpoints(
        self, location: str, cluster: str
    ) -> dict[str, Any] | None:
        """Get the latest effective setpoints."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2
                       ORDER BY timestamp DESC LIMIT 1""",
                    location,
                    cluster,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get effective setpoints: {e}")
        return None

    async def log_effective_setpoint(
        self,
        location: str,
        cluster: str,
        mode: str | None,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        vpd: float | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Log effective setpoint to setpoint_history (for ramp tracking).

        .. deprecated:: Use log_effective_setpoints (plural) instead.

        This is called during ramps to log the effective setpoint at each change.

        Args:
            location: Location name
            cluster: Cluster name
            mode: Mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT/TRANSITION) or None
            heating_setpoint: Effective heating setpoint
            cooling_setpoint: Effective cooling setpoint
            humidity: Effective humidity setpoint
            co2: Effective CO2 setpoint
            vpd: Effective VPD setpoint
            timestamp: Timestamp (default: NOW())

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                db_mode = mode if mode else None
                ts = timestamp or datetime.now()

                await conn.execute(
                    """
                    INSERT INTO setpoint_history (timestamp, location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                    ts,
                    location,
                    cluster,
                    db_mode,
                    heating_setpoint,
                    cooling_setpoint,
                    humidity,
                    co2,
                    vpd,
                )

                return True
        except Exception as e:
            logger.error(f"Failed to log effective setpoint: {e}")
            return False

    async def log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        mode: str | None,
        effective_heating_setpoint: float | None = None,
        effective_cooling_setpoint: float | None = None,
        effective_humidity_setpoint: float | None = None,
        effective_co2_setpoint: float | None = None,
        effective_vpd_setpoint: float | None = None,
        nominal_heating_setpoint: float | None = None,
        nominal_cooling_setpoint: float | None = None,
        nominal_humidity_setpoint: float | None = None,
        nominal_co2_setpoint: float | None = None,
        nominal_vpd_setpoint: float | None = None,
        ramp_progress_heating: float | None = None,
        ramp_progress_cooling: float | None = None,
        ramp_progress_humidity: float | None = None,
        ramp_progress_co2: float | None = None,
        ramp_progress_vpd: float | None = None,
        device_name: str | None = None,
        effective_light_intensity: float | None = None,
        nominal_light_intensity: float | None = None,
        ramp_progress_light: float | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Log effective setpoints to effective_setpoints table.

        This is the newer version that logs both effective and nominal values
        along with ramp progress for all setpoint types.

        Note: The batching and Redis write behavior from DatabaseManager
        will be handled at the facade level during wiring.

        Args:
            location: Location name
            cluster: Cluster name
            mode: Current mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT) or None
            effective_*: Actual values being used after ramp
            nominal_*: Target values from database
            ramp_progress_*: Progress values (0.0-1.0) or None if not ramping
            device_name: Device name for per-device logging
            timestamp: Optional timestamp (defaults to NOW())

        Returns:
            True if buffered successfully, False otherwise
        """
        try:
            ts = timestamp or datetime.now()
            db_mode = mode if mode else None

            # Buffer the record for batch writing (performance optimization)
            record = {
                "timestamp": ts,
                "location": location,
                "cluster": cluster,
                "mode": db_mode,
                "device_name": device_name,
                "effective_heating_setpoint": effective_heating_setpoint,
                "effective_cooling_setpoint": effective_cooling_setpoint,
                "effective_humidity_setpoint": effective_humidity_setpoint,
                "effective_co2_setpoint": effective_co2_setpoint,
                "effective_vpd_setpoint": effective_vpd_setpoint,
                "effective_light_intensity": effective_light_intensity,
                "nominal_heating_setpoint": nominal_heating_setpoint,
                "nominal_cooling_setpoint": nominal_cooling_setpoint,
                "nominal_humidity_setpoint": nominal_humidity_setpoint,
                "nominal_co2_setpoint": nominal_co2_setpoint,
                "nominal_vpd_setpoint": nominal_vpd_setpoint,
                "nominal_light_intensity": nominal_light_intensity,
                "ramp_progress_heating": ramp_progress_heating,
                "ramp_progress_cooling": ramp_progress_cooling,
                "ramp_progress_humidity": ramp_progress_humidity,
                "ramp_progress_co2": ramp_progress_co2,
                "ramp_progress_vpd": ramp_progress_vpd,
                "ramp_progress_light": ramp_progress_light,
            }

            self._batch_buffer.append(record)

            # Check if it's time to flush the batch
            current_time = time.time()
            if current_time - self._last_batch_flush >= self._batch_interval:
                await self.flush_batch_buffer()

            # Write effective setpoints to Redis immediately for real-time access
            # State keys = fast truth for automation, Streams = history for dashboards/DB
            if self._redis_client and getattr(self._redis_client, "redis_enabled", False):
                self._redis_client.write_effective_setpoints(
                    location=location,
                    cluster=cluster,
                    effective_heating_setpoint=effective_heating_setpoint,
                    effective_cooling_setpoint=effective_cooling_setpoint,
                    effective_humidity_setpoint=effective_humidity_setpoint,
                    effective_co2_setpoint=effective_co2_setpoint,
                    effective_vpd_setpoint=effective_vpd_setpoint,
                    device_name=device_name,
                    effective_light_intensity=effective_light_intensity,
                    nominal_heating_setpoint=nominal_heating_setpoint,
                    nominal_cooling_setpoint=nominal_cooling_setpoint,
                    nominal_humidity_setpoint=nominal_humidity_setpoint,
                    nominal_co2_setpoint=nominal_co2_setpoint,
                    nominal_vpd_setpoint=nominal_vpd_setpoint,
                    ramp_progress_heating=ramp_progress_heating,
                    ramp_progress_cooling=ramp_progress_cooling,
                    ramp_progress_humidity=ramp_progress_humidity,
                    ramp_progress_co2=ramp_progress_co2,
                    ramp_progress_vpd=ramp_progress_vpd,
                    mode=mode,
                )

            return True
        except Exception as e:
            logger.error(f"Error buffering effective setpoints: {e}")
            return False

    def invalidate_cache_for_location_cluster(self, location: str, cluster: str) -> None:
        """Clear all cached entries matching location/cluster pattern.

        Args:
            location: Location name
            cluster: Cluster name
        """
        # Patterns to look for in cache keys
        # Format for get_setpoint cache key: get_setpoint:location:cluster:mode
        # Format from BaseRepository._get_cache_key: operation:arg1:arg2:...
        target_pattern = f":{location}:{cluster}:"

        to_delete = [
            key
            for key in self._query_cache
            if key.startswith("get_setpoint:") and target_pattern in key
        ]

        for key in to_delete:
            del self._query_cache[key]

        if to_delete:
            logger.debug(
                f"Invalidated {len(to_delete)} setpoint cache entries for {location}/{cluster}"
            )

    def invalidate_all_cache(self) -> None:
        """Full cache clear for this repository."""
        self.clear_cache()
