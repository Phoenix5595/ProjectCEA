"""Background tasks for automation control loop."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from app.alarm_manager import AlarmManager
from app.control.control_engine import ControlEngine
from app.database import DatabaseManager
from app.events.operational_ports import OperationalEventSink
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from app.services.mode_transition_service import ModeTransitionService
from shared.infra_logging import get_logger

from .auto_persist import AutoPersistMixin
from .batch_flush import BatchFlushMixin
from .calendar import CalendarMixin
from .config_events import ConfigEventsMixin
from .control_loop import ControlLoopMixin
from .heartbeat import HeartbeatMixin
from .setpoint_history import SetpointHistoryMixin
from .state_sync import StateSyncMixin

logger = get_logger(__name__)


CONTROL_LOOP_INTERVAL_MAX = 5  # seconds, non-negotiable


class BackgroundTasks(
    ControlLoopMixin,
    StateSyncMixin,
    HeartbeatMixin,
    AutoPersistMixin,
    SetpointHistoryMixin,
    BatchFlushMixin,
    ConfigEventsMixin,
    CalendarMixin,
):
    """Manages background automation tasks.

    This class has been refactored to demonstrate the worker pattern,
    with individual methods for each background task. In the future,
    this can be migrated to use the shared worker framework.
    """

    def __init__(
        self,
        control_engine: ControlEngine,
        database: DatabaseManager,
        update_interval: int = 1,
        alarm_manager: AlarmManager | None = None,
        event_sink: OperationalEventSink | None = None,
        *,
        mode_transition_service: ModeTransitionService,
    ):
        """Initialize background tasks.

        Args:
            control_engine: Control engine instance
            database: Database manager instance
            update_interval: Control loop interval in seconds (1–5, clamped)
            alarm_manager: Optional alarm manager instance
            mode_transition_service: Required activation owner shared by the
                calendar catch-up and periodic application paths
        """
        self.control_engine = control_engine
        self.database = database
        self.alarm_manager = alarm_manager
        self.operational_event_sink = event_sink
        self.mode_transition_service = mode_transition_service
        self.update_interval = max(1, min(CONTROL_LOOP_INTERVAL_MAX, int(update_interval)))
        self._running = False
        self._task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._auto_persist_task: asyncio.Task | None = None
        self._setpoint_history_task: asyncio.Task | None = None
        self._batch_flush_task: asyncio.Task | None = None
        self._config_event_task: asyncio.Task | None = None
        self._calendar_sync_task: asyncio.Task | None = None
        self._last_calendar_mode_tick: float = 0.0
        self._calendar_mode_interval = 60.0
        self._calendar_scheduler: CalendarModeScheduler | None = None
        self._control_failure_count = 0
        self._control_success_count = 0
        self._degraded_active = False
        self._last_control_error_time: float = 0.0
        self._consecutive_control_errors: int = 0
        self._last_tick_completed_at: float | None = None
        self._scheduler_retry_task: asyncio.Task | None = None
        self._scheduler_ready = asyncio.Event()
        self._scheduler_loaded_once = False

    async def start(self) -> None:
        """Start background control loop and tasks."""
        if self._running:
            logger.warning("Background tasks already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._control_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._auto_persist_task = asyncio.create_task(self._auto_persist_loop())
        self._setpoint_history_task = asyncio.create_task(self._setpoint_history_loop())
        # Schedule refresh loop removed: event-driven via config events
        self._batch_flush_task = asyncio.create_task(self._batch_flush_loop())
        self._config_event_task = asyncio.create_task(self._config_event_consumer_loop())
        self._calendar_sync_task = asyncio.create_task(self._calendar_sync_loop())

        # Load all scheduler data BEFORE first control loop tick
        try:
            await self._load_scheduler_data()
            self._scheduler_ready.set()
            logger.info("Scheduler data loaded and startup gate opened")
        except Exception as e:
            logger.error(f"Failed to load scheduler data at startup: {e}", exc_info=True)
            # Gate remains unset; control loop will block until data is loaded.
            self._scheduler_retry_task = asyncio.create_task(self._retry_scheduler_load())

        try:
            scheduler = CalendarModeScheduler(
                self.database,
                self.operational_event_sink,
                transition_service=self.mode_transition_service,
            )
            await scheduler.run_catchup()
        except Exception as e:
            logger.warning("Calendar mode catch-up failed: %s", e)
        logger.info(
            f"Background control loop started (interval: {self.update_interval}s) - event-driven schedule refresh"
        )
        logger.info(
            "Heartbeat, auto-persist, setpoint history, batch flush, and config event consumer tasks started"
        )

    async def _retry_scheduler_load(self) -> None:
        """Retry the startup scheduler load until the database is available."""
        while self._running:
            await asyncio.sleep(5)
            try:
                await self._load_scheduler_data()
                self._scheduler_ready.set()
                logger.info("Scheduler data loaded after retry")
                return
            except Exception as e:
                logger.warning("Scheduler data retry failed: %s", e)

    def control_loop_check(self) -> dict[str, Any]:
        """Report whether a control tick completed within the expected interval."""
        if not self._running or self._task is None or self._task.done():
            return {"ok": False, "detail": "control loop task not running"}
        if self._last_tick_completed_at is None:
            return {"ok": False, "detail": "no completed control tick"}

        age = time.monotonic() - self._last_tick_completed_at
        limit = 3 * self._current_control_interval()
        if age > limit:
            return {"ok": False, "detail": f"last control tick {age:.1f}s ago (limit {limit}s)"}
        return {"ok": True, "age_ms": round(age * 1000, 1)}

    async def stop(self) -> None:
        """Stop background control loop and tasks."""
        self._running = False

        # Cancel all tasks
        tasks = [
            self._task,
            self._heartbeat_task,
            self._auto_persist_task,
            self._setpoint_history_task,
            self._batch_flush_task,
            self._config_event_task,
            self._calendar_sync_task,
            self._scheduler_retry_task,
        ]
        for task in tasks:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        logger.info("Background control loop and tasks stopped")
