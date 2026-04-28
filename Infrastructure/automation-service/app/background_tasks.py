"""Background tasks for automation control loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json

from app.alarm_manager import AlarmManager
from app.control.control_engine import ControlEngine
from app.control.schedule_merge import merge_schedules_with_config
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)


CONTROL_LOOP_INTERVAL_MAX = 5  # seconds, non-negotiable


class BackgroundTasks:
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
        self._control_failure_count = 0
        self._control_success_count = 0
        self._degraded_active = False

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
        ]
        for task in tasks:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Background control loop and tasks stopped")

    def set_update_interval(self, interval: int) -> None:
        """Update control loop interval (1–5s, clamped).

        Args:
            interval: New interval in seconds
        """
        self.update_interval = max(1, min(CONTROL_LOOP_INTERVAL_MAX, int(interval)))
        logger.info(f"Control loop interval updated to {self.update_interval}s")

    async def _control_loop(self) -> None:
        """Main control loop - refactored as a worker pattern."""
        retry_delay = 1.0
        max_retry_delay = 60.0

        while self._running:
            try:
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

                # Run control loop (worker pattern execution)
                # Debug logging removed
                await self.control_engine.run_control_loop()
                # Debug logging removed
                await self._record_control_success()

                # Reset retry delay on success
                retry_delay = 1.0

                # Wait for next iteration
                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._record_control_failure(str(e))
                logger.error(f"Error in control loop: {e}", exc_info=True)
                # Continue running even on error
                await asyncio.sleep(self.update_interval)

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

    async def _heartbeat_loop(self) -> None:
        """Heartbeat task - writes automation service heartbeat."""
        heartbeat_interval = 30  # Every 30 seconds

        while self._running:
            try:
                await asyncio.sleep(heartbeat_interval)

                # Write automation service heartbeat (worker pattern execution)
                if (
                    self.database._automation_redis
                    and self.database._automation_redis.redis_enabled
                ):
                    self.database._automation_redis.write_heartbeat("automation-service")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)

    async def _auto_persist_loop(self) -> None:
        """Auto-persist task - syncs Redis PID parameters to database."""
        persist_interval = 60  # Every minute

        while self._running:
            try:
                await asyncio.sleep(persist_interval)

                if (
                    not self.database._automation_redis
                    or not self.database._automation_redis.redis_enabled
                ):
                    continue

                # Sync PID parameters from Redis to DB (worker pattern execution)
                device_types = ["heater", "co2"]
                synced_count = 0

                for device_type in device_types:
                    try:
                        redis_params = self.database._automation_redis.read_pid_parameters(
                            device_type
                        )
                        if redis_params:
                            # Check if different from DB
                            db_params = await self.database.pid_repo.get_pid_parameters(device_type)
                            if db_params:
                                # Compare and update if different
                                if (
                                    redis_params.get("kp") != db_params["kp"]
                                    or redis_params.get("ki") != db_params["ki"]
                                    or redis_params.get("kd") != db_params["kd"]
                                ):
                                    await self.database.pid_repo.set_pid_parameters(
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

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-persist loop: {e}", exc_info=True)

    async def _setpoint_history_loop(self) -> None:
        """Setpoint history task - logs current setpoints to history table."""
        history_interval = 300  # Every 5 minutes

        while self._running:
            try:
                await asyncio.sleep(history_interval)

                if not self.database._db_connected:
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

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in setpoint history loop: {e}", exc_info=True)

    # Removed legacy _schedule_refresh_loop in favor of event-driven config changes

    async def _batch_flush_loop(self) -> None:
        """Batch flush task - periodically flushes batched database writes."""
        flush_interval = 10.0  # Every 10 seconds

        while self._running:
            try:
                await asyncio.sleep(flush_interval)

                # Flush batched records (worker pattern execution)
                flushed_count = await self.database.setpoint_repo.flush_batch_buffer()
                if flushed_count > 0:
                    logger.debug(f"Flushed {flushed_count} batched records")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch flush loop: {e}", exc_info=True)

    async def _config_event_consumer_loop(self) -> None:
        """Consume config change events and update scheduler immediately.

        This runs as a background task that subscribes to the ConfigEventBus
        and reacts to configuration changes by updating the scheduler immediately
        (bypassing the 60-second refresh interval).

        Events handled:
        - RAMP_TIMES_CHANGED: Refreshes schedules from database and updates scheduler
        """
        from app.events import ConfigEventType, get_event_bus

        event_bus = get_event_bus()
        logger.info("Config event consumer started")

        try:
            async for event in event_bus.subscribe():
                try:
                    logger.info(
                        "Processing config event: "
                        + f"{event.event_type.value} for {event.location}/{event.cluster}"
                    )

                    if event.event_type == ConfigEventType.RAMP_TIMES_CHANGED:
                        # Fetch fresh schedules and update scheduler
                        if not self.database._db_connected:
                            logger.warning(
                                "Database not connected, cannot refresh schedules for event"
                            )
                            continue

                        db_schedules = await self.database.schedule_repo.get_schedules()
                        if self.control_engine.scheduler:
                            merged = merge_schedules_with_config(
                                db_schedules, self.control_engine.config
                            )
                            self.control_engine.scheduler.update_schedules(merged)
                            logger.info(
                                "Scheduler updated with "
                                + f"{len(merged)} schedules after config change event"
                            )

                    if event.event_type == ConfigEventType.SCHEDULE_CHANGED:
                        # Same handling as RAMP_TIMES_CHANGED - refresh schedules
                        if not self.database._db_connected:
                            logger.warning(
                                "Database not connected, cannot refresh schedules for event"
                            )
                            continue

                        db_schedules = await self.database.schedule_repo.get_schedules()
                        if self.control_engine.scheduler:
                            merged = merge_schedules_with_config(
                                db_schedules, self.control_engine.config
                            )
                            self.control_engine.scheduler.update_schedules(merged)
                            logger.info(
                                "Scheduler updated with "
                                + f"{len(merged)} schedules after SCHEDULE_CHANGED event"
                            )

                except Exception as e:
                    logger.error(f"Error processing config event: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Config event consumer stopped")
            raise
