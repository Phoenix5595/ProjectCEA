"""Background tasks for automation control loop."""
import asyncio
import logging
from typing import Optional
from app.control.control_engine import ControlEngine
from app.database import DatabaseManager
from app.alarm_manager import AlarmManager

logger = logging.getLogger(__name__)


class BackgroundTasks:
    """Manages background automation tasks."""
    
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
        logger.info(f"Background control loop started (interval: {self.update_interval}s)")
        logger.info("Heartbeat, auto-persist, and setpoint history tasks started")
    
    async def stop(self) -> None:
        """Stop background control loop and tasks."""
        self._running = False
        
        # Cancel all tasks
        tasks = [self._task, self._heartbeat_task, self._auto_persist_task, self._setpoint_history_task]
        for task in tasks:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        logger.info("Background control loop and tasks stopped")
    
    async def _control_loop(self) -> None:
        """Main control loop."""
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
                
                # Run control loop
                await self.control_engine.run_control_loop()
                
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
    
    def set_update_interval(self, interval: int) -> None:
        """Update control loop interval.
        
        Args:
            interval: New interval in seconds
        """
        self.update_interval = interval
        logger.info(f"Control loop interval updated to {interval}s")
    
    async def _heartbeat_loop(self) -> None:
        """Heartbeat task - writes automation service heartbeat and checks sensor heartbeats."""
        heartbeat_interval = 2  # Write heartbeat every 2 seconds
        
        while self._running:
            try:
                # Write automation service heartbeat
                if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                    self.database._automation_redis.write_heartbeat('automation-service')
                    
                    # Check sensor heartbeats and update last good values
                    # This would check for sensor:clusterA, sensor:clusterB, etc.
                    # For now, we'll just write our own heartbeat
                    # Sensor gateways should write their own heartbeats
                
                await asyncio.sleep(heartbeat_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
                await asyncio.sleep(heartbeat_interval)
    
    async def _auto_persist_loop(self) -> None:
        """Auto-persist task - syncs Redis to database periodically."""
        persist_interval = 60  # Sync every 60 seconds
        
        while self._running:
            try:
                await asyncio.sleep(persist_interval)
                
                if not self.database._automation_redis or not self.database._automation_redis.redis_enabled:
                    continue
                
                # Sync setpoints from Redis to DB (if changed)
                # The database is already the source of truth for API-set setpoints
                
                # Sync PID parameters from Redis to DB (if changed)
                # This ensures any changes made via API are persisted
                device_types = ['heater', 'co2']
                for device_type in device_types:
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
                                logger.debug(f"Synced PID parameters for {device_type} from Redis to DB")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auto-persist loop: {e}", exc_info=True)
    
    async def _setpoint_history_loop(self) -> None:
        """Setpoint history task - logs current setpoints to history table every 5 minutes."""
        history_interval = 300  # Log every 5 minutes (300 seconds)
        
        while self._running:
            try:
                await asyncio.sleep(history_interval)
                
                if not self.database._db_connected:
                    continue
                
                # Get all current setpoints and log them to history
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

