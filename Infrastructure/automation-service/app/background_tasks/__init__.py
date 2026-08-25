"""Background tasks for automation control loop."""

from __future__ import annotations

import asyncio
import contextlib

from app.alarm_manager import AlarmManager
from app.control.control_engine import ControlEngine
from app.database import DatabaseManager
from app.services.calendar_mode_scheduler import CalendarModeScheduler
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
    ):
        """Initialize background tasks.

        Args:
            control_engine: Control engine instance
            database: Database manager instance
            update_interval: Control loop interval in seconds (1–5, clamped)
            alarm_manager: Optional alarm manager instance
        """
        self.control_engine = control_engine
        self.database = database
        self.alarm_manager = alarm_manager
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
        self._calendar_sync_interval = 300.0
        self._last_calendar_sync_tick: float = 0.0
        self._control_failure_count = 0
        self._control_success_count = 0
        self._degraded_active = False
        self._last_control_error_time: float = 0.0
        self._consecutive_control_errors: int = 0
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
            # Gate remains unset; control loop will block until data is loaded

        try:
            scheduler = CalendarModeScheduler(self.database)
            await scheduler.run_catchup()
        except Exception as e:
            logger.warning("Calendar mode catch-up failed: %s", e)
        logger.info(
            f"Background control loop started (interval: {self.update_interval}s) - event-driven schedule refresh"
        )
        logger.info(
            "Heartbeat, auto-persist, setpoint history, batch flush, and config event consumer tasks started"
        )

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
        ]
        for task in tasks:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        logger.info("Background control loop and tasks stopped")
