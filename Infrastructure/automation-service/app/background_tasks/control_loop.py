"""Control loop task with degraded-mode handling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import time

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ControlLoopMixin:
    """Mixin for the main control loop and failure tracking."""

    async def _control_loop(self) -> None:
        """Main control loop - refactored as a worker pattern with fixed-rate scheduling."""
        retry_delay = 1.0
        max_retry_delay = 60.0

        while self._running:
            try:
                # Startup gate: wait for scheduler data load before first tick
                if not self._scheduler_ready.is_set():
                    await self._scheduler_ready.wait()

                # Check database connection
                if not self.database._db_connected:
                    # Try to reconnect
                    try:
                        await self.database._connect_db()
                        self.database._db_connected = True
                        retry_delay = 1.0
                        logger.info("Database connection restored")
                    except Exception as e:
                        await self._record_control_failure(f"database reconnect failed: {e}")
                        logger.warning(
                            f"Database connection failed: {e}. Retrying in {retry_delay}s..."
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, max_retry_delay)
                        continue

                # Fixed-rate scheduling: record deadline before execution
                deadline = time.monotonic() + self.update_interval

                # Run control loop (worker pattern execution)
                await self.control_engine.run_control_loop()
                await self._maybe_run_calendar_mode_scheduler()
                await self._record_control_success()

                # Reset retry delay on success
                retry_delay = 1.0

                # Sleep until deadline, or skip if we already exceeded it (tick overrun handling)
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    # Tick exceeded interval — log warning and catch up immediately
                    logger.warning(
                        f"Control loop tick exceeded interval by {-remaining * 1000:.1f}ms "
                        f"(interval={self.update_interval}s), skipping sleep to catch up"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._record_control_failure(str(e))
                # Adaptive backoff: track error time, cap iteration rate at 1/s
                now = time.monotonic()
                if (
                    self._last_control_error_time > 0
                    and now - self._last_control_error_time < self.update_interval
                ):
                    # Second+ consecutive error: rate-limit logging
                    self._consecutive_control_errors += 1
                    if self._consecutive_control_errors % 10 == 0:
                        logger.warning(
                            f"Control loop error (x{self._consecutive_control_errors}): {e}"
                        )
                else:
                    # First error or after reset: log at ERROR
                    self._consecutive_control_errors = 1
                    logger.error(f"Error in control loop: {e}", exc_info=True)
                self._last_control_error_time = now
                # Prevent tight spin: sleep for update_interval before retry
                await asyncio.sleep(self.update_interval)
                continue

    async def _record_control_failure(self, reason: str) -> None:
        """Enter degraded mode after repeated control-loop failures."""
        self._control_failure_count += 1
        self._control_success_count = 0
        if self._control_failure_count < 3 and not self._degraded_active:
            return

        self._degraded_active = True
        payload = {
            "active": True,
            "reason": reason,
            "failure_count": self._control_failure_count,
            "success_count": self._control_success_count,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self._write_degraded_state(payload)
        logger.warning(
            "Control loop degraded mode active after %s consecutive failures: %s",
            self._control_failure_count,
            reason,
        )

    async def _record_control_success(self) -> None:
        """Clear degraded mode after a stable success window."""
        self._control_failure_count = 0
        if not self._degraded_active:
            return

        self._control_success_count += 1
        if self._control_success_count < 10:
            payload = {
                "active": True,
                "reason": "recovering",
                "failure_count": self._control_failure_count,
                "success_count": self._control_success_count,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            await self._write_degraded_state(payload)
            return

        self._degraded_active = False
        payload = {
            "active": False,
            "reason": "recovered after 10 successful control ticks",
            "failure_count": self._control_failure_count,
            "success_count": self._control_success_count,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self._write_degraded_state(payload)
        logger.info("Control loop degraded mode cleared after 10 successful ticks")

    async def _write_degraded_state(self, payload: dict[str, object]) -> None:
        """Best-effort write of the control-loop degraded state to Redis."""
        redis_client = None
        if self.database._automation_redis and self.database._automation_redis.redis_enabled:
            redis_client = self.database._automation_redis.redis_client
        if redis_client is None:
            logger.debug("Skipping automation:degraded write; Redis unavailable")
            return
        await asyncio.to_thread(redis_client.set, "automation:degraded", json.dumps(payload))
