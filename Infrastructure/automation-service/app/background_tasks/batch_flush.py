"""Batch flush loop for batched database writes."""

from __future__ import annotations

import asyncio
import time

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class BatchFlushMixin:
    """Mixin for periodically flushing batched database writes."""

    async def _batch_flush_loop(self) -> None:
        """Batch flush task - periodically flushes batched database writes."""
        flush_interval = 10.0  # Every 10 seconds

        while self._running:
            try:
                deadline = time.monotonic() + flush_interval

                # Flush batched records (worker pattern execution)
                flushed_count = await self.database.setpoint_repo.flush_batch_buffer()
                if flushed_count > 0:
                    logger.debug(f"Flushed {flushed_count} batched records")

                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    logger.warning(
                        f"batch flush loop tick exceeded interval by {-remaining * 1000:.1f}ms, skipping sleep"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch flush loop: {e}", exc_info=True)
                continue
