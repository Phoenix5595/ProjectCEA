"""Background tasks for automation control loop."""
from shared.logging import get_logger
import asyncio
from typing import Optional
from app.control.control_engine import ControlEngine
from app.database import DatabaseManager
from app.alarm_manager import AlarmManager
from shared.logging import get_logger

logger = get_logger(__name__)


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
        alarm_manager: Optional[AlarmManager] = None
    ):
        """Initialize background tasks.

        Args:
            control_engine: Control engine instance
            database: Database manager instance
            update_interval: Control loop interval in seconds (default: 1)
            alarm_manager: Optional alarm manager instance
        """
        self.control_engine = control_engine
        self.database = database
        self.alarm_manager = alarm_manager
        self.update_interval = update_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._auto_persist_task: Optional[asyncio.Task] = None
        self._setpoint_history_task: Optional[asyncio.Task] = None
        self._schedule_refresh_task: Optional[asyncio.Task] = None
        self._batch_flush_task: Optional[asyncio.Task] = None

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
        self._schedule_refresh_task = asyncio.create_task(self._schedule_refresh_loop())
        self._batch_flush_task = asyncio.create_task(self._batch_flush_loop())
        logger.info(f"Background control loop started (interval: {self.update_interval}s)")
        logger.info("Heartbeat, auto-persist, setpoint history, schedule refresh, and batch flush tasks started")

    async def stop(self) -> None:
        """Stop background control loop and tasks."""
        self._running = False

        # Cancel all tasks
        tasks = [self._task, self._heartbeat_task, self._auto_persist_task, self._setpoint_history_task, self._schedule_refresh_task, self._batch_flush_task]
        for task in tasks:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Background control loop and tasks stopped")

    def set_update_interval(self, interval: int) -> None:
        """Update control loop interval.

        Args:
            interval: New interval in seconds
        """
        self.update_interval = interval
        logger.info(f"Control loop interval updated to {interval}s")

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
                        logger.warning(f"Database connection failed: {e}. Retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, max_retry_delay)
                        continue

                # Run control loop (worker pattern execution)
                # #region agent log
                import json
                import time
                loop_start = time.time()
                # #endregion
                await self.control_engine.run_control_loop()
                # #region agent log
                loop_duration = time.time() - loop_start
                try:
                    with open('/home/antoine/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps({
                            'sessionId': 'debug-session',
                            'runId': 'run1',
                            'hypothesisId': 'A',
                            'location': 'background_tasks.py:111',
                            'message': 'control_loop_iteration',
                            'data': {
                                'duration_seconds': loop_duration,
                                'update_interval': self.update_interval,
                                'sleep_time': self.update_interval
                            },
                            'timestamp': int(time.time() * 1000)
                        }) + '\n')
                except: pass
                # #endregion

                # Reset retry delay on success
                retry_delay = 1.0

                # Wait for next iteration
                await asyncio.sleep(self.update_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in control loop: {e}", exc_info=True)
                # Continue running even on error
                await asyncio.sleep(self.update_interval)

    async def _heartbeat_loop(self) -> None:
        """Heartbeat task - writes automation service heartbeat."""
        heartbeat_interval = 30  # Every 30 seconds

        while self._running:
            try:
                await asyncio.sleep(heartbeat_interval)

                # Write automation service heartbeat (worker pattern execution)
                if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                    self.database._automation_redis.write_heartbeat('automation-service')

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

                if not self.database._automation_redis or not self.database._automation_redis.redis_enabled:
                    continue

                # Sync PID parameters from Redis to DB (worker pattern execution)
                device_types = ['heater', 'co2']
                synced_count = 0

                for device_type in device_types:
                    try:
                        redis_params = self.database._automation_redis.read_pid_parameters(device_type)
                        if redis_params:
                            # Check if different from DB
                            db_params = await self.database.get_pid_parameters(device_type)
                            if db_params:
                                # Compare and update if different
                                if (redis_params.get('kp') != db_params['kp'] or
                                    redis_params.get('ki') != db_params['ki'] or
                                    redis_params.get('kd') != db_params['kd']):
                                    await self.database.set_pid_parameters(
                                        device_type,
                                        redis_params['kp'],
                                        redis_params['ki'],
                                        redis_params['kd'],
                                        source=redis_params.get('source', 'api')
                                    )
                                    synced_count += 1
                                    logger.debug(f"Synced PID parameters for {device_type} from Redis to DB")
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
                            await conn.execute("""
                                INSERT INTO setpoint_history (timestamp, location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                                VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
                            """, row['location'], row['cluster'], row['mode'],
                                row['heating_setpoint'], row['cooling_setpoint'], row['humidity'], row['co2'], row['vpd'])

                        if rows:
                            logger.debug(f"Logged {len(rows)} setpoint snapshots to history")

                except Exception as e:
                    logger.error(f"Error logging setpoint history: {e}", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in setpoint history loop: {e}", exc_info=True)

    async def _schedule_refresh_loop(self) -> None:
        """Schedule refresh task - reloads schedules from database."""
        refresh_interval = 60  # Every minute

        while self._running:
            try:
                await asyncio.sleep(refresh_interval)

                if not self.database._db_connected:
                    continue

                # Refresh schedules (worker pattern execution)
                db_schedules = await self.database.get_schedules()

                # Update scheduler in control engine
                if self.control_engine.scheduler:
                    self.control_engine.scheduler.update_schedules(db_schedules)
                    logger.debug(f"Refreshed {len(db_schedules)} schedules from database")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error refreshing schedules: {e}", exc_info=True)

    async def _batch_flush_loop(self) -> None:
        """Batch flush task - periodically flushes batched database writes."""
        flush_interval = 10.0  # Every 10 seconds

        while self._running:
            try:
                await asyncio.sleep(flush_interval)

                # Flush batched records (worker pattern execution)
                flushed_count = await self.database.flush_batch_buffer()
                if flushed_count > 0:
                    logger.debug(f"Flushed {flushed_count} batched records")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch flush loop: {e}", exc_info=True)

