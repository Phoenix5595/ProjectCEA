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
                self._calendar_scheduler = CalendarModeScheduler(
                    self.database,
                    self.operational_event_sink,
                    transition_service=self.mode_transition_service,
                )
            await self._calendar_scheduler.run_tick()
        except Exception as e:
            logger.warning("Calendar mode scheduler tick failed: %s", e)

    async def _calendar_sync_loop(self) -> None:
        worker = CalendarSyncWorker(self.database)
        active: dict[int, asyncio.Task] = {}
        loop = asyncio.get_running_loop()
        try:
            while self._running:
                started = loop.time()
                try:
                    for event_id, task in tuple(active.items()):
                        if task.done():
                            del active[event_id]
                            task.result()
                    due = await self.database.calendar_repo.events_pending_sync()
                    for row in due:
                        if len(active) >= 4:
                            break
                        if row["id"] not in active:
                            active[row["id"]] = asyncio.create_task(
                                worker.sync_events((row["id"],), deadline_seconds=5.0)
                            )
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.warning("calendar_background_unavailable")
                await asyncio.sleep(max(0.0, 1.0 - (loop.time() - started)))
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)
