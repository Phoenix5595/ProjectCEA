"""Calendar mode scheduler and sync loops."""

from __future__ import annotations

import asyncio
import time

from app.calendar.sync_worker import CalendarSyncWorker
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class CalendarMixin:
    """Mixin for calendar mode scheduler and sync worker loops."""

    async def _maybe_run_calendar_mode_scheduler(self) -> None:
        now = time.monotonic()
        if now - self._last_calendar_mode_tick < self._calendar_mode_interval:
            return
        self._last_calendar_mode_tick = now
        try:
            if self._calendar_scheduler is None:
                self._calendar_scheduler = CalendarModeScheduler(self.database)
            await self._calendar_scheduler.run_tick()
        except Exception as e:
            logger.warning("Calendar mode scheduler tick failed: %s", e)

    async def _calendar_sync_loop(self) -> None:
        import time

        worker = CalendarSyncWorker(self.database)
        while self._running:
            try:
                now = time.monotonic()
                if now - self._last_calendar_sync_tick >= self._calendar_sync_interval:
                    self._last_calendar_sync_tick = now
                    await worker.run_sync()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Calendar sync loop error: %s", e)
                await asyncio.sleep(60)
