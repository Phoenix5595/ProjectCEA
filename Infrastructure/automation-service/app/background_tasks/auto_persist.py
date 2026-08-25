"""Auto-persist loop for syncing Redis PID parameters to database."""

from __future__ import annotations

import asyncio
import time

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class AutoPersistMixin:
    """Mixin for auto-persisting PID parameters from Redis to database."""

    async def _auto_persist_loop(self) -> None:
        """Auto-persist task - syncs Redis PID parameters to database."""
        persist_interval = 60  # Every minute

        while self._running:
            try:
                deadline = time.monotonic() + persist_interval

                if (
                    not self.database._automation_redis
                    or not self.database._automation_redis.redis_enabled
                ):
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
                    else:
                        logger.warning(
                            f"auto-persist loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                        )
                    continue

                # Sync PID parameters from Redis to DB (worker pattern execution)
                device_types = ["heater", "co2"]
                synced_count = 0
                default_location = "Flower Room"
                default_cluster = "main"

                for device_type in device_types:
                    try:
                        redis_params = self.database._automation_redis.read_pid_parameters(
                            device_type
                        )
                        if redis_params:
                            # Check if different from DB
                            db_params = await self.database.pid_repo.get_pid_parameters(
                                default_location, default_cluster, device_type
                            )
                            if db_params and (
                                # Compare and update if different
                                redis_params.get("kp") != db_params["kp"]
                                or redis_params.get("ki") != db_params["ki"]
                                or redis_params.get("kd") != db_params["kd"]
                            ):
                                await self.database.pid_repo.set_pid_parameters(
                                    default_location,
                                    default_cluster,
                                    device_type,
                                    redis_params["kp"],
                                    redis_params["ki"],
                                    redis_params["kd"],
                                    source=redis_params.get("source", "api"),
                                )
                                synced_count += 1
                                logger.debug(
                                    f"Synced PID parameters for {device_type} from Redis to DB"
                                )
                    except Exception as e:
                        logger.error(f"Error syncing PID parameters for {device_type}: {e}")

                if synced_count > 0:
                    logger.info(f"Auto-persisted {synced_count} PID parameter sets")

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    logger.warning(
                        f"auto-persist loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-persist loop: {e}", exc_info=True)
                continue
