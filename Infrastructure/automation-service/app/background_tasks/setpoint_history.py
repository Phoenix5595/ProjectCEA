"""Setpoint history logging loop."""

from __future__ import annotations

import asyncio
import time

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class SetpointHistoryMixin:
    """Mixin for logging current setpoints to history table."""

    async def _setpoint_history_loop(self) -> None:
        """Setpoint history task - logs current setpoints to history table."""
        history_interval = 300  # Every 5 minutes

        while self._running:
            try:
                deadline = time.monotonic() + history_interval

                if not self.database._db_connected:
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    else:
                        logger.warning(
                            f"setpoint history loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                        )
                    continue

                # Log setpoint history (worker pattern execution)
                try:
                    pool = await self.database._get_pool()
                    async with pool.acquire() as conn:
                        # Get all distinct location/cluster/mode combinations with latest setpoints
                        rows = await conn.fetch("""
                            SELECT DISTINCT ON (location, cluster, mode)
                                location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd
                            FROM setpoints
                            WHERE heating_setpoint IS NOT NULL OR cooling_setpoint IS NOT NULL OR humidity IS NOT NULL OR co2 IS NOT NULL OR vpd IS NOT NULL
                            ORDER BY location, cluster, mode, updated_at DESC
                        """)

                        # Insert current setpoints into history
                        for row in rows:
                            await conn.execute(
                                """
                                INSERT INTO setpoint_history (timestamp, location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                                VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                                row["location"],
                                row["cluster"],
                                row["mode"],
                                row["heating_setpoint"],
                                row["cooling_setpoint"],
                                row["humidity"],
                                row["co2"],
                                row["vpd"],
                            )

                        if rows:
                            logger.debug(f"Logged {len(rows)} setpoint snapshots to history")

                except Exception as e:
                    logger.error(f"Error logging setpoint history: {e}", exc_info=True)

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    logger.warning(
                        f"setpoint history loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in setpoint history loop: {e}", exc_info=True)
                continue
