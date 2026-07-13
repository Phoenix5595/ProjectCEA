"""Heartbeat loop for automation service liveness."""

from __future__ import annotations

import asyncio
import time

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class HeartbeatMixin:
    """Mixin for automation service heartbeat loop."""

    async def _heartbeat_loop(self) -> None:
        """Heartbeat task - writes automation service heartbeat."""
        heartbeat_interval = 30  # Every 30 seconds

        while self._running:
            try:
                deadline = time.monotonic() + heartbeat_interval

                # Write automation service heartbeat (worker pattern execution)
                if (
                    self.database._automation_redis
                    and self.database._automation_redis.redis_enabled
                ):
                    self.database._automation_redis.write_heartbeat("automation-service")

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    logger.warning(
                        f"heartbeat loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
                continue
