from __future__ import annotations

import asyncio
from datetime import datetime
import time
from typing import TYPE_CHECKING, Any

from shared.db_errors import is_transient_db_error
from shared.replay_buffer import ReplayBuffer

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class SetpointRepository(BaseRepository):
    """Repository for effective setpoint logging operations (setpoints table deprecated)."""

    def __init__(self, pool: Pool | None = None, redis_client: Any = None) -> None:
        super().__init__(pool)
        self._redis_client = redis_client
        self._batch_buffer: ReplayBuffer[dict[str, Any]] = ReplayBuffer(
            12_000, name="effective_setpoints"
        )
        # Reduced from 10s to 5s for faster dashboard updates (trade-off: more DB writes)
        self._batch_interval = 5.0
        self._last_batch_flush = time.time()
        self._flush_lock = asyncio.Lock()

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def flush_batch_buffer(self) -> int:
        """Flush batched effective setpoint logs to database.

        Returns:
            Number of records flushed (0 if no records to flush)
        """
        async with self._flush_lock:
            if not self._batch_buffer:
                return 0

            flushed_count = 0
            insert_query = """
                    INSERT INTO effective_setpoints (
                        "time", location, cluster, device_name, mode,
                        effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
                        effective_co2_setpoint, effective_vpd_setpoint,
                        nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
                        nominal_co2_setpoint, nominal_vpd_setpoint,
                        ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
                        ramp_progress_co2, ramp_progress_vpd,
                        effective_light_intensity, nominal_light_intensity, ramp_progress_light
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)
                """
            while self._batch_buffer:
                batch_data = self._batch_buffer.peek(2_000)
                try:
                    async with self.pool.acquire() as conn:
                        await conn.executemany(
                            insert_query,
                            [
                                (
                                    record["time"],
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
                except Exception as error:
                    batch_count = len(batch_data)
                    if is_transient_db_error(error):
                        logger.warning(
                            "Failed to flush batch buffer; retaining %d effective setpoint rows: %s",
                            batch_count,
                            error,
                        )
                        break

                    self._batch_buffer.consume(batch_count)
                    logger.error(
                        "Dropped %d effective setpoint rows after permanent database error: %s",
                        batch_count,
                        error,
                        exc_info=True,
                    )
                    continue

                self._batch_buffer.consume(len(batch_data))
                flushed_count += len(batch_data)
                logger.debug("Flushed %d batched effective setpoint records", len(batch_data))

            self._last_batch_flush = time.time()
            return flushed_count

    async def get_latest_effective_setpoints(
        self, location: str, cluster: str
    ) -> dict[str, Any] | None:
        """Get the latest effective setpoints."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2
                       ORDER BY "time" DESC LIMIT 1""",
                    location,
                    cluster,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get effective setpoints: {e}")
        return None

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
            mode: Current period name from climate_periods or None
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
                "time": ts,
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

            accepted = self._batch_buffer.offer([record])

            # Check if it's time to flush the batch
            current_time = time.time()
            if current_time - self._last_batch_flush >= self._batch_interval:
                await self.flush_batch_buffer()

            # Write effective setpoints to Redis immediately for real-time access
            # Redis state keys provide fast access; history is buffered above.
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
                    nominal_light_intensity=nominal_light_intensity,
                    ramp_progress_light=ramp_progress_light,
                )

            return accepted == 1
        except Exception as e:
            logger.error(f"Error buffering effective setpoints: {e}")
            return False
