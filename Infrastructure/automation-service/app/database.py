"""Database manager for TimescaleDB operations."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import time as dt_time

# Standard library imports
import os
import time
from typing import Any

# Third-party imports
import asyncpg
import redis

# Local imports
from shared.logging import get_logger

from .redis_client import AutomationRedisClient
from .repositories import (
    ControlActionRepository,
    DeviceRepository,
    PIDRepository,
    RoomModeRepository,
    ScheduleRepository,
    SensorRepository,
    SetpointRepository,
)

logger = get_logger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for automation service."""

    def __init__(self, db_config: dict[str, Any] | None = None, redis_url: str | None = None):
        """Initialize database manager.

        Args:
            db_config: Database connection config dict with host, database, user, password, port.
                      If None, uses environment variables or defaults.
            redis_url: Redis connection URL. If None, uses environment variable or default.
        """
        if db_config is None:
            password = os.getenv("POSTGRES_PASSWORD")
            if not password:
                raise ValueError("POSTGRES_PASSWORD environment variable is required")
            self.db_config = {
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "database": os.getenv("POSTGRES_DB", "cea_sensors"),
                "user": os.getenv("POSTGRES_USER", "cea_user"),
                "password": password,
                "port": int(os.getenv("POSTGRES_PORT", "5432")),
            }
        else:
            self.db_config = db_config
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._pool: asyncpg.Pool | None = None
        self._redis_client: redis.Redis | None = None
        self._redis_enabled = False
        self._automation_redis: AutomationRedisClient | None = None
        self._db_connected = False
        self._retry_delay = 1.0  # Initial retry delay in seconds
        self._max_retry_delay = 60.0  # Maximum retry delay

        # Performance optimization: Query result caching and batching
        self._query_cache: dict[str, tuple[Any, float]] = {}  # cache_key -> (result, expiry_time)
        self._cache_ttl = 30.0  # 30 seconds cache TTL for control loop queries
        self._batch_buffer: list[
            dict[str, Any]
        ] = []  # Buffer for batched effective setpoint logging
        self._batch_interval = 10.0  # Flush batch every 10 seconds
        self._last_batch_flush = time.time()
        self._control_loop_cache: dict[str, Any] = {}  # Cache for current control loop iteration

        # Performance optimization: Query result caching
        self._query_cache: dict[str, tuple[Any, float]] = {}  # cache_key -> (result, expiry_time)
        self._cache_ttl = 30.0  # 30 seconds cache TTL for control loop queries
        self._batch_buffer: list[
            dict[str, Any]
        ] = []  # Buffer for batched effective setpoint logging
        self._batch_interval = 10.0  # Flush batch every 10 seconds
        self._last_batch_flush = time.time()

        # Repository instances (initialized in initialize())
        self._sensor_repo: SensorRepository | None = None
        self._device_repo: DeviceRepository | None = None
        self._setpoint_repo: SetpointRepository | None = None
        self._schedule_repo: ScheduleRepository | None = None
        self._pid_repo: PIDRepository | None = None
        self._room_mode_repo: RoomModeRepository | None = None
        self._control_action_repo: ControlActionRepository | None = None

    async def initialize(self) -> bool:
        """Initialize database connection and create tables.

        Returns:
            True if successful, False otherwise
        """
        try:
            await self._connect_db()
            await self._create_tables()
            await self._migrate_tables()
            await self._create_room_modes_tables()
            await self._connect_redis()
            # Initialize automation Redis client for stream and state writes
            self._automation_redis = AutomationRedisClient(redis_url=self.redis_url, redis_ttl=10)
            self._automation_redis.connect()

            # Initialize repository instances with the connection pool
            self._sensor_repo = SensorRepository(self._pool)
            self._device_repo = DeviceRepository(self._pool)
            self._setpoint_repo = SetpointRepository(self._pool, self._automation_redis)
            self._schedule_repo = ScheduleRepository(self._pool)
            self._pid_repo = PIDRepository(self._pool)
            self._room_mode_repo = RoomModeRepository(self._pool)
            self._control_action_repo = ControlActionRepository(self._pool)

            return True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            return False

    def _get_cache_key(self, operation: str, *args) -> str:
        """Generate a cache key for query results."""
        return f"{operation}:{':'.join(str(arg) for arg in args)}"

    def _get_cached_result(self, cache_key: str) -> Any | None:
        """Get result from cache if valid, None otherwise."""
        if cache_key in self._query_cache:
            result, expiry_time = self._query_cache[cache_key]
            if time.time() < expiry_time:
                return result
            else:
                # Expired, remove from cache
                del self._query_cache[cache_key]
        return None

    def _set_cached_result(self, cache_key: str, result: Any) -> None:
        """Store result in cache with TTL."""
        expiry_time = time.time() + self._cache_ttl
        self._query_cache[cache_key] = (result, expiry_time)

    def clear_cache(self) -> None:
        """Clear all cached query results."""
        self._query_cache.clear()
        logger.info("Database query cache cleared")

    async def flush_batch_buffer(self) -> int:
        """Flush batched effective setpoint logs to database.

        Returns:
            Number of records flushed (0 if no records to flush)
        """
        if not self._batch_buffer:
            return 0

        flushed_count = 0
        batch_data: list[dict[str, Any]] = []
        try:
            # Use a single batch insert for all buffered records
            batch_data = self._batch_buffer.copy()
            self._batch_buffer.clear()

            if batch_data:
                # Insert all records in a single transaction
                async with self._pool.acquire() as conn:  # pyright: ignore[reportOptionalMemberAccess]
                    # Prepare the insert statement
                    insert_query = """

                            INSERT INTO effective_setpoints (
                                timestamp, location, cluster, device_name, mode,
                                effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
                                effective_co2_setpoint, effective_vpd_setpoint,
                                nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
                                nominal_co2_setpoint, nominal_vpd_setpoint,
                                ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
                                ramp_progress_co2, ramp_progress_vpd,
                                effective_light_intensity, nominal_light_intensity, ramp_progress_light
                            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)
                        """.strip()
                    await conn.executemany(
                        insert_query,
                        [
                            (
                                record["timestamp"],
                                record["location"],
                                record["cluster"],
                                record["device_name"],
                                record.get("mode"),
                                record["effective_heating_setpoint"],
                                record["effective_cooling_setpoint"],
                                record["effective_humidity_setpoint"],
                                record["effective_co2_setpoint"],
                                record["effective_vpd_setpoint"],
                                record["nominal_heating_setpoint"],
                                record["nominal_cooling_setpoint"],
                                record["nominal_humidity_setpoint"],
                                record["nominal_co2_setpoint"],
                                record["nominal_vpd_setpoint"],
                                record["ramp_progress_heating"],
                                record["ramp_progress_cooling"],
                                record["ramp_progress_humidity"],
                                record["ramp_progress_co2"],
                                record["ramp_progress_vpd"],
                                record["effective_light_intensity"],
                                record["nominal_light_intensity"],
                                record["ramp_progress_light"],
                            )
                            for record in batch_data
                        ],
                    )

                    flushed_count = len(batch_data)
                logger.debug(f"Flushed {flushed_count} batched effective setpoint records")

        except Exception as e:
            logger.error(f"Failed to flush batch buffer: {e}", exc_info=True)
            # Re-add failed records to buffer for retry
            self._batch_buffer.extend(batch_data)

        self._last_batch_flush = time.time()
        return flushed_count

    async def _connect_db(self) -> None:
        """Connect to TimescaleDB with retry logic."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self._pool = await asyncpg.create_pool(
                    host=self.db_config["host"],
                    database=self.db_config["database"],
                    user=self.db_config["user"],
                    password=self.db_config["password"],
                    port=self.db_config["port"],
                    min_size=2,
                    max_size=10,
                    command_timeout=30,  # Query timeout in seconds
                    server_settings={"application_name": "automation_service"},
                )
                self._db_connected = True
                self._retry_delay = 1.0  # Reset retry delay on success
                logger.info("Connected to TimescaleDB")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = min(self._retry_delay * (2**attempt), self._max_retry_delay)
                    logger.warning(
                        f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise ConnectionError(
                        f"Failed to connect to TimescaleDB after {max_retries} attempts: {e}"
                    )

    async def _migrate_tables(self) -> None:
        """Migrate tables to add missing columns."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Add missing columns to effective_setpoints table
            await conn.execute("""
                ALTER TABLE effective_setpoints
                ADD COLUMN IF NOT EXISTS effective_light_intensity REAL,
                ADD COLUMN IF NOT EXISTS nominal_light_intensity REAL,
                ADD COLUMN IF NOT EXISTS ramp_progress_light REAL
            """)

            # Add missing columns to automation_state table
            await conn.execute("""
                ALTER TABLE automation_state
                ADD COLUMN IF NOT EXISTS schedule_ramp_up_duration INTEGER,
                ADD COLUMN IF NOT EXISTS schedule_ramp_down_duration INTEGER,
                ADD COLUMN IF NOT EXISTS schedule_photoperiod_hours REAL,
                ADD COLUMN IF NOT EXISTS pid_kp REAL,
                ADD COLUMN IF NOT EXISTS pid_ki REAL,
                ADD COLUMN IF NOT EXISTS pid_kd REAL
            """)

            # Add PID control mode columns to pid_parameters table
            await conn.execute("""
                ALTER TABLE pid_parameters
                ADD COLUMN IF NOT EXISTS control_mode TEXT DEFAULT 'pid'
                    CHECK (control_mode IN ('auto_pid', 'pid', 'on_off')),
                ADD COLUMN IF NOT EXISTS hysteresis_high REAL DEFAULT 1.0,
                ADD COLUMN IF NOT EXISTS hysteresis_low REAL DEFAULT 0.5
            """)

            # Add change_reason column to pid_parameter_history table
            await conn.execute("""
                ALTER TABLE pid_parameter_history
                ADD COLUMN IF NOT EXISTS change_reason TEXT
            """)

            # Create pid_autotune_state table for tracking auto-tuning progress
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pid_autotune_state (
                    device_type TEXT PRIMARY KEY,
                    is_active BOOLEAN DEFAULT FALSE,
                    started_at TIMESTAMPTZ,
                    cycles_completed INTEGER DEFAULT 0,
                    current_amplitude REAL,
                    current_period REAL,
                    current_ku REAL,
                    current_tu REAL,
                    suggested_kp REAL,
                    suggested_ki REAL,
                    suggested_kd REAL,
                    last_change_reason TEXT,
                    last_update TIMESTAMPTZ,
                    status TEXT DEFAULT 'idle'
                        CHECK (status IN ('idle', 'running', 'calculating', 'complete', 'error'))
                )
            """)

            logger.info("Database migration completed")

    async def _connect_redis(self) -> None:
        """Connect to Redis."""
        try:
            self._redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            self._redis_client.ping()
            self._redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url}")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will use TimescaleDB fallback.")
            self._redis_enabled = False

    async def _create_tables(self) -> None:
        """Create all required tables if they don't exist."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Device states table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS device_states (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    state INTEGER NOT NULL CHECK (state IN (0, 1)),
                    mode TEXT NOT NULL CHECK (mode IN ('manual', 'auto', 'scheduled')),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(location, cluster, device_name)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_states_location_cluster 
                ON device_states(location, cluster)
            """)

            # Control history table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS control_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW() CHECK (timestamp >= '2020-01-01'::timestamptz AND timestamp <= NOW() + INTERVAL '1 day'),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    old_state INTEGER CHECK (old_state IS NULL OR old_state IN (0, 1)),
                    new_state INTEGER CHECK (new_state IS NULL OR new_state IN (0, 1)),
                    mode TEXT CHECK (mode IS NULL OR mode IN ('manual', 'auto', 'scheduled')),
                    reason TEXT,
                    sensor_value REAL CHECK (sensor_value IS NULL OR (sensor_value > -1e6 AND sensor_value < 1e6)),
                    setpoint REAL CHECK (setpoint IS NULL OR (setpoint > -1e6 AND setpoint < 1e6))
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('control_history', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning("TimescaleDB extension not available, using regular table")

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_control_history_location 
                ON control_history(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_control_history_timestamp 
                ON control_history(timestamp DESC)
            """)

            # Setpoints table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS setpoints (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    heating_setpoint REAL CHECK (heating_setpoint IS NULL OR (heating_setpoint >= -50 AND heating_setpoint <= 100)),
                    cooling_setpoint REAL CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100)),
                    humidity REAL CHECK (humidity IS NULL OR (humidity >= 0 AND humidity <= 100)),
                    co2 REAL CHECK (co2 IS NULL OR (co2 >= 0 AND co2 <= 10000)),
                    vpd REAL CHECK (vpd IS NULL OR (vpd >= 0 AND vpd <= 10)),
                    mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION')),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # Migration: Rename temperature to heating_setpoint if temperature column exists
            try:
                # Check if temperature column exists
                result = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'setpoints' AND column_name = 'temperature'
                """)
                if result:
                    # Rename temperature to heating_setpoint
                    await conn.execute("""
                        ALTER TABLE setpoints RENAME COLUMN temperature TO heating_setpoint
                    """)
                    logger.info("Migrated temperature column to heating_setpoint")
            except Exception as e:
                logger.warning(f"Migration check for temperature column failed: {e}")

            # Add cooling_setpoint column if it doesn't exist
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS cooling_setpoint REAL 
                    CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100))
                """)
            except Exception:
                pass  # Column might already exist

            # Add mode and vpd columns if they don't exist (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS mode TEXT
                """)
            except Exception:
                pass  # Column might already exist

            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS vpd REAL
                """)
            except Exception:
                pass  # Column might already exist

            # Add ramp_in_duration column for setpoint ramping (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS ramp_in_duration INTEGER
                    CHECK (ramp_in_duration IS NULL OR (ramp_in_duration >= 0 AND ramp_in_duration <= 240))
                """)
            except Exception:
                pass  # Column might already exist

            # Update mode constraint to include PRE_DAY and PRE_NIGHT
            # Note: We can't directly modify CHECK constraints, so we add a new constraint
            # PostgreSQL will enforce both, but the new one is more permissive
            # For existing databases, we'll rely on application-level validation
            try:
                # Try to drop the old constraint if it exists (PostgreSQL auto-names it)
                # We'll add a new constraint that allows PRE_DAY and PRE_NIGHT
                await conn.execute("""
                    ALTER TABLE setpoints DROP CONSTRAINT IF EXISTS setpoints_mode_check
                """)
            except Exception:
                pass  # Constraint might not exist or have different name
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD CONSTRAINT setpoints_mode_check 
                    CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION', 'PRE_DAY', 'PRE_NIGHT'))
                """)
            except Exception:
                # If constraint already exists with new values, that's fine
                pass

            # Drop unique constraint/index to allow history inserts, then add a non-unique index
            try:
                await conn.execute("""
                    ALTER TABLE setpoints DROP CONSTRAINT IF EXISTS setpoints_location_cluster_key
                """)
            except Exception:
                pass  # Constraint might not exist
            try:
                await conn.execute("""
                    ALTER TABLE setpoints DROP CONSTRAINT IF EXISTS setpoints_location_cluster_mode_key
                """)
            except Exception:
                pass
            try:
                await conn.execute("""
                    DROP INDEX IF EXISTS setpoints_location_cluster_mode_key
                """)
            except Exception:
                pass
            try:
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_setpoints_loc_cluster_mode_updated_at
                    ON setpoints(location, cluster, mode, updated_at DESC)
                """)
            except Exception:
                pass  # Index might already exist

            # Schedules table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    day_of_week INTEGER,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE,
                    mode TEXT,  -- DAY, NIGHT, TRANSITION, PRE_DAY, or PRE_NIGHT for mode-based scheduling
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Add mode column if it doesn't exist (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS mode TEXT
                """)
            except Exception:
                pass  # Column might already exist

            # Add ramp columns for light intensity ramping (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS target_intensity REAL
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS ramp_up_duration INTEGER
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS ramp_down_duration INTEGER
                """)
            except Exception:
                pass  # Columns might already exist

            # Add pre-day and pre-night duration columns for climate schedules (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS pre_day_duration INTEGER
                    CHECK (pre_day_duration IS NULL OR (pre_day_duration >= 0 AND pre_day_duration <= 240))
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS pre_night_duration INTEGER
                    CHECK (pre_night_duration IS NULL OR (pre_night_duration >= 0 AND pre_night_duration <= 240))
                """)
            except Exception:
                pass  # Columns might already exist

            # Add updated_at column for optimistic locking (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                """)
                # For existing rows, set updated_at = created_at if it's NULL (shouldn't happen with DEFAULT, but safety check)
                await conn.execute("""
                    UPDATE schedules SET updated_at = created_at WHERE updated_at IS NULL
                """)
            except Exception:
                pass  # Column might already exist

            # Rules table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    condition_sensor TEXT NOT NULL,
                    condition_operator TEXT NOT NULL,
                    condition_value REAL NOT NULL,
                    action_device TEXT NOT NULL,
                    action_state INTEGER NOT NULL,
                    priority INTEGER DEFAULT 0,
                    schedule_id INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
                )
            """)

            # Automation state table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS automation_state (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    device_state INTEGER NOT NULL,
                    device_mode TEXT NOT NULL,
                    pid_output REAL,
                    duty_cycle_percent REAL,
                    active_rule_ids INTEGER[],
                    active_schedule_ids INTEGER[],
                    control_reason TEXT,
                    schedule_ramp_up_duration INTEGER,
                    schedule_ramp_down_duration INTEGER,
                    schedule_photoperiod_hours REAL,
                    pid_kp REAL,
                    pid_ki REAL,
                    pid_kd REAL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('automation_state', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning(
                    "TimescaleDB extension not available for automation_state, using regular table"
                )

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_location 
                ON automation_state(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_timestamp 
                ON automation_state(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_device 
                ON automation_state(location, cluster, device_name)
            """)

            # Effective setpoints table (TimescaleDB hypertable)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS effective_setpoints (
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    mode TEXT,
                    device_name TEXT,
                    effective_heating_setpoint REAL,
                    effective_cooling_setpoint REAL,
                    effective_humidity_setpoint REAL,
                    effective_co2_setpoint REAL,
                    effective_vpd_setpoint REAL,
                    effective_light_intensity REAL,
                    nominal_heating_setpoint REAL,
                    nominal_cooling_setpoint REAL,
                    nominal_humidity_setpoint REAL,
                    nominal_co2_setpoint REAL,
                    nominal_vpd_setpoint REAL,
                    nominal_light_intensity REAL,
                    ramp_progress_heating REAL,
                    ramp_progress_cooling REAL,
                    ramp_progress_humidity REAL,
                    ramp_progress_co2 REAL,
                    ramp_progress_vpd REAL,
                    ramp_progress_light REAL
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('effective_setpoints', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning(
                    "TimescaleDB extension not available for effective_setpoints, using regular table"
                )

            # Migration: Add columns for humidity, co2, and vpd if they don't exist
            try:
                # Check if effective_humidity_setpoint column exists
                column_check = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'effective_setpoints' 
                    AND column_name = 'effective_humidity_setpoint'
                """)

                if column_check is None:
                    # Add new columns for humidity, co2, and vpd
                    await conn.execute("""
                        ALTER TABLE effective_setpoints
                        ADD COLUMN IF NOT EXISTS effective_humidity_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS effective_co2_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS effective_vpd_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_humidity_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_co2_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_vpd_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_humidity REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_co2 REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_vpd REAL
                    """)
                    logger.info("Added humidity, co2, and vpd columns to effective_setpoints table")
            except Exception as e:
                logger.warning(f"Error adding columns to effective_setpoints: {e}")

            # Migration: Add columns for light intensity if they don't exist
            try:
                light_column_check = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'effective_setpoints' 
                    AND column_name = 'effective_light_intensity'
                """)

                if light_column_check is None:
                    # Add new columns for light intensity
                    await conn.execute("""
                        ALTER TABLE effective_setpoints
                        ADD COLUMN IF NOT EXISTS device_name TEXT,
                        ADD COLUMN IF NOT EXISTS effective_light_intensity REAL,
                        ADD COLUMN IF NOT EXISTS nominal_light_intensity REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_light REAL
                    """)
                    logger.info(
                        "Added device_name and light intensity columns to effective_setpoints table"
                    )
            except Exception as e:
                logger.warning(f"Error adding light intensity columns to effective_setpoints: {e}")

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location 
                ON effective_setpoints(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp 
                ON effective_setpoints(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_device 
                ON effective_setpoints(location, cluster, device_name, timestamp DESC)
            """)

            # PID parameters table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pid_parameters (
                    device_type TEXT PRIMARY KEY,
                    kp REAL NOT NULL,
                    ki REAL NOT NULL,
                    kd REAL NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by TEXT,
                    source TEXT
                )
            """)

            # PID parameter history table (for audit trail)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pid_parameter_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    device_type TEXT NOT NULL,
                    kp REAL NOT NULL,
                    ki REAL NOT NULL,
                    kd REAL NOT NULL,
                    updated_by TEXT,
                    source TEXT
                )
            """)

            # Config versions table (for audit trail of all config changes)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS config_versions (
                    version_id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    author TEXT,
                    comment TEXT,
                    config_type TEXT NOT NULL,  -- 'setpoint', 'schedule', 'pid', 'safety'
                    location TEXT,
                    cluster TEXT,
                    changes JSONB  -- Store the actual changes made
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_config_versions_timestamp 
                ON config_versions(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_config_versions_type 
                ON config_versions(config_type)
            """)

            # PID parameter history index
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pid_parameter_history_device_type 
                ON pid_parameter_history(device_type, timestamp DESC)
            """)

            # Device mappings table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS device_mappings (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    active_high BOOLEAN NOT NULL DEFAULT TRUE,
                    safe_state INTEGER NOT NULL CHECK (safe_state IN (0, 1)),
                    mcp_board_id INTEGER,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(location, cluster, device_name)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_mappings_location_cluster 
                ON device_mappings(location, cluster)
            """)

            logger.info("Database tables created/verified")

    async def log_config_version(
        self,
        config_type: str,
        author: str | None = None,
        comment: str | None = None,
        location: str | None = None,
        cluster: str | None = None,
        changes: dict[str, Any] | None = None,
    ) -> int | None:
        """Log a configuration change to config_versions table.

        Args:
            config_type: Type of config change ('setpoint', 'schedule', 'pid', 'safety')
            author: Author of the change (optional)
            comment: Comment describing the change (optional)
            location: Location name if applicable (optional)
            cluster: Cluster name if applicable (optional)
            changes: Dictionary of changes made (optional)

        Returns:
            version_id if successful, None otherwise
        """
        try:
            import json

            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO config_versions 
                    (timestamp, author, comment, config_type, location, cluster, changes)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6)
                    RETURNING version_id
                """,
                    author,
                    comment,
                    config_type,
                    location,
                    cluster,
                    json.dumps(changes) if changes else None,
                )
                return row["version_id"] if row else None
        except Exception as e:
            logger.error(f"Error logging config version: {e}")
            return None

    async def _create_tables(self) -> None:
        """Create all required tables if they don't exist."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Device states table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS device_states (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    state INTEGER NOT NULL CHECK (state IN (0, 1)),
                    mode TEXT NOT NULL CHECK (mode IN ('manual', 'auto', 'scheduled')),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(location, cluster, device_name)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_states_location_cluster 
                ON device_states(location, cluster)
            """)

            # Control history table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS control_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW() CHECK (timestamp >= '2020-01-01'::timestamptz AND timestamp <= NOW() + INTERVAL '1 day'),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    old_state INTEGER CHECK (old_state IS NULL OR old_state IN (0, 1)),
                    new_state INTEGER CHECK (new_state IS NULL OR new_state IN (0, 1)),
                    mode TEXT CHECK (mode IS NULL OR mode IN ('manual', 'auto', 'scheduled')),
                    reason TEXT,
                    sensor_value REAL CHECK (sensor_value IS NULL OR (sensor_value > -1e6 AND sensor_value < 1e6)),
                    setpoint REAL CHECK (setpoint IS NULL OR (setpoint > -1e6 AND setpoint < 1e6))
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('control_history', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning("TimescaleDB extension not available, using regular table")

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_control_history_location 
                ON control_history(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_control_history_timestamp 
                ON control_history(timestamp DESC)
            """)

            # Setpoints table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS setpoints (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    heating_setpoint REAL CHECK (heating_setpoint IS NULL OR (heating_setpoint >= -50 AND heating_setpoint <= 100)),
                    cooling_setpoint REAL CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100)),
                    humidity REAL CHECK (humidity IS NULL OR (humidity >= 0 AND humidity <= 100)),
                    co2 REAL CHECK (co2 IS NULL OR (co2 >= 0 AND co2 <= 10000)),
                    vpd REAL CHECK (vpd IS NULL OR (vpd >= 0 AND vpd <= 10)),
                    mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION')),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # Setpoint history table (time-series for Grafana)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS setpoint_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW() CHECK (timestamp >= '2020-01-01'::timestamptz AND timestamp <= NOW() + INTERVAL '1 day'),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION', 'PRE_DAY', 'PRE_NIGHT')),
                    heating_setpoint REAL CHECK (heating_setpoint IS NULL OR (heating_setpoint >= -50 AND heating_setpoint <= 100)),
                    cooling_setpoint REAL CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100)),
                    humidity REAL CHECK (humidity IS NULL OR (humidity >= 0 AND humidity <= 100)),
                    co2 REAL CHECK (co2 IS NULL OR (co2 >= 0 AND co2 <= 10000)),
                    vpd REAL CHECK (vpd IS NULL OR (vpd >= 0 AND vpd <= 10))
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('setpoint_history', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning(
                    "TimescaleDB extension not available for setpoint_history, using regular table"
                )

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_setpoint_history_location 
                ON setpoint_history(location, cluster, mode)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_setpoint_history_timestamp 
                ON setpoint_history(timestamp DESC)
            """)

            # Migration: Rename temperature to heating_setpoint if temperature column exists (second location)
            try:
                # Check if temperature column exists in setpoints
                result = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'setpoints' AND column_name = 'temperature'
                """)
                if result:
                    # Rename temperature to heating_setpoint
                    await conn.execute("""
                        ALTER TABLE setpoints RENAME COLUMN temperature TO heating_setpoint
                    """)
                    logger.info("Migrated temperature column to heating_setpoint (second location)")
            except Exception as e:
                logger.warning(
                    f"Migration check for temperature column failed (second location): {e}"
                )

            # Migration: Rename temperature to heating_setpoint in setpoint_history if it exists
            try:
                result = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'setpoint_history' AND column_name = 'temperature'
                """)
                if result:
                    await conn.execute("""
                        ALTER TABLE setpoint_history RENAME COLUMN temperature TO heating_setpoint
                    """)
                    logger.info(
                        "Migrated temperature column to heating_setpoint in setpoint_history"
                    )
            except Exception as e:
                logger.warning(
                    f"Migration check for temperature column in setpoint_history failed: {e}"
                )

            # Add cooling_setpoint column to setpoints if it doesn't exist
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS cooling_setpoint REAL 
                    CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100))
                """)
            except Exception:
                pass  # Column might already exist

            # Add cooling_setpoint column to setpoint_history if it doesn't exist
            try:
                await conn.execute("""
                    ALTER TABLE setpoint_history ADD COLUMN IF NOT EXISTS cooling_setpoint REAL 
                    CHECK (cooling_setpoint IS NULL OR (cooling_setpoint >= -50 AND cooling_setpoint <= 100))
                """)
            except Exception:
                pass  # Column might already exist

            # Add mode and vpd columns if they don't exist (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS mode TEXT
                """)
            except Exception:
                pass  # Column might already exist

            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS vpd REAL
                """)
            except Exception:
                pass  # Column might already exist

            # Add ramp_in_duration column for setpoint ramping (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE setpoints ADD COLUMN IF NOT EXISTS ramp_in_duration INTEGER
                    CHECK (ramp_in_duration IS NULL OR (ramp_in_duration >= 0 AND ramp_in_duration <= 240))
                """)
            except Exception:
                pass  # Column might already exist

            # Drop old unique constraint if it exists and create new one
            try:
                await conn.execute("""
                    ALTER TABLE setpoints DROP CONSTRAINT IF EXISTS setpoints_location_cluster_key
                """)
            except Exception:
                pass  # Constraint might not exist

            # Create new unique constraint with mode
            try:
                await conn.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS setpoints_location_cluster_mode_key 
                    ON setpoints(location, cluster, mode)
                """)
            except Exception:
                pass  # Index might already exist

            # Schedules table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    day_of_week INTEGER,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE,
                    mode TEXT,  -- DAY, NIGHT, TRANSITION, PRE_DAY, or PRE_NIGHT for mode-based scheduling
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Add mode column if it doesn't exist (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS mode TEXT
                """)
            except Exception:
                pass  # Column might already exist

            # Add ramp columns for light intensity ramping (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS target_intensity REAL
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS ramp_up_duration INTEGER
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS ramp_down_duration INTEGER
                """)
            except Exception:
                pass  # Columns might already exist

            # Add pre-day and pre-night duration columns for climate schedules (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS pre_day_duration INTEGER
                    CHECK (pre_day_duration IS NULL OR (pre_day_duration >= 0 AND pre_day_duration <= 240))
                """)
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS pre_night_duration INTEGER
                    CHECK (pre_night_duration IS NULL OR (pre_night_duration >= 0 AND pre_night_duration <= 240))
                """)
            except Exception:
                pass  # Columns might already exist

            # Add updated_at column for optimistic locking (for existing databases)
            try:
                await conn.execute("""
                    ALTER TABLE schedules ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                """)
                # For existing rows, set updated_at = created_at if it's NULL (shouldn't happen with DEFAULT, but safety check)
                await conn.execute("""
                    UPDATE schedules SET updated_at = created_at WHERE updated_at IS NULL
                """)
            except Exception:
                pass  # Column might already exist

            # Rules table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    condition_sensor TEXT NOT NULL,
                    condition_operator TEXT NOT NULL,
                    condition_value REAL NOT NULL,
                    action_device TEXT NOT NULL,
                    action_state INTEGER NOT NULL,
                    priority INTEGER DEFAULT 0,
                    schedule_id INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    FOREIGN KEY (schedule_id) REFERENCES schedules(id) ON DELETE SET NULL
                )
            """)

            # Automation state table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS automation_state (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    device_state INTEGER NOT NULL,
                    device_mode TEXT NOT NULL,
                    pid_output REAL,
                    duty_cycle_percent REAL,
                    active_rule_ids INTEGER[],
                    active_schedule_ids INTEGER[],
                    control_reason TEXT,
                    schedule_ramp_up_duration INTEGER,
                    schedule_ramp_down_duration INTEGER,
                    schedule_photoperiod_hours REAL,
                    pid_kp REAL,
                    pid_ki REAL,
                    pid_kd REAL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('automation_state', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning(
                    "TimescaleDB extension not available for automation_state, using regular table"
                )

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_location 
                ON automation_state(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_timestamp 
                ON automation_state(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_automation_state_device 
                ON automation_state(location, cluster, device_name)
            """)

            # Effective setpoints table (TimescaleDB hypertable)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS effective_setpoints (
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    mode TEXT,
                    device_name TEXT,
                    effective_heating_setpoint REAL,
                    effective_cooling_setpoint REAL,
                    effective_humidity_setpoint REAL,
                    effective_co2_setpoint REAL,
                    effective_vpd_setpoint REAL,
                    effective_light_intensity REAL,
                    nominal_heating_setpoint REAL,
                    nominal_cooling_setpoint REAL,
                    nominal_humidity_setpoint REAL,
                    nominal_co2_setpoint REAL,
                    nominal_vpd_setpoint REAL,
                    nominal_light_intensity REAL,
                    ramp_progress_heating REAL,
                    ramp_progress_cooling REAL,
                    ramp_progress_humidity REAL,
                    ramp_progress_co2 REAL,
                    ramp_progress_vpd REAL,
                    ramp_progress_light REAL
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('effective_setpoints', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning(
                    "TimescaleDB extension not available for effective_setpoints, using regular table"
                )

            # Migration: Add columns for humidity, co2, and vpd if they don't exist
            try:
                # Check if effective_humidity_setpoint column exists
                column_check = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'effective_setpoints' 
                    AND column_name = 'effective_humidity_setpoint'
                """)

                if column_check is None:
                    # Add new columns for humidity, co2, and vpd
                    await conn.execute("""
                        ALTER TABLE effective_setpoints
                        ADD COLUMN IF NOT EXISTS effective_humidity_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS effective_co2_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS effective_vpd_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_humidity_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_co2_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS nominal_vpd_setpoint REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_humidity REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_co2 REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_vpd REAL
                    """)
                    logger.info("Added humidity, co2, and vpd columns to effective_setpoints table")
            except Exception as e:
                logger.warning(f"Error adding columns to effective_setpoints: {e}")

            # Migration: Add columns for light intensity if they don't exist
            try:
                light_column_check = await conn.fetchval("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'effective_setpoints' 
                    AND column_name = 'effective_light_intensity'
                """)

                if light_column_check is None:
                    # Add new columns for light intensity
                    await conn.execute("""
                        ALTER TABLE effective_setpoints
                        ADD COLUMN IF NOT EXISTS device_name TEXT,
                        ADD COLUMN IF NOT EXISTS effective_light_intensity REAL,
                        ADD COLUMN IF NOT EXISTS nominal_light_intensity REAL,
                        ADD COLUMN IF NOT EXISTS ramp_progress_light REAL
                    """)
                    logger.info(
                        "Added device_name and light intensity columns to effective_setpoints table"
                    )
            except Exception as e:
                logger.warning(f"Error adding light intensity columns to effective_setpoints: {e}")

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location 
                ON effective_setpoints(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp 
                ON effective_setpoints(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_device 
                ON effective_setpoints(location, cluster, device_name, timestamp DESC)
            """)

            # PID parameters table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pid_parameters (
                    device_type TEXT PRIMARY KEY,
                    kp REAL NOT NULL,
                    ki REAL NOT NULL,
                    kd REAL NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_by TEXT,
                    source TEXT
                )
            """)

            # PID parameter history table (for audit trail)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pid_parameter_history (
                    id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    device_type TEXT NOT NULL,
                    kp REAL NOT NULL,
                    ki REAL NOT NULL,
                    kd REAL NOT NULL,
                    updated_by TEXT,
                    source TEXT
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pid_parameter_history_device_type 
                ON pid_parameter_history(device_type, timestamp DESC)
            """)

            # Config versions table (for audit trail of all config changes)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS config_versions (
                    version_id BIGSERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    author TEXT,
                    comment TEXT,
                    config_type TEXT NOT NULL,  -- 'setpoint', 'schedule', 'pid', 'safety'
                    location TEXT,
                    cluster TEXT,
                    changes JSONB  -- Store the actual changes made
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_config_versions_timestamp 
                ON config_versions(timestamp DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_config_versions_type 
                ON config_versions(config_type)
            """)

            # Device mappings table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS device_mappings (
                    id BIGSERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    channel INTEGER NOT NULL CHECK (channel >= 0 AND channel <= 15),
                    active_high BOOLEAN NOT NULL DEFAULT TRUE,
                    safe_state INTEGER NOT NULL CHECK (safe_state IN (0, 1)),
                    mcp_board_id INTEGER,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(location, cluster, device_name)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_device_mappings_location_cluster 
                ON device_mappings(location, cluster)
            """)

            logger.info("Database tables created/verified")

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool with retry logic."""
        if self._pool is None or not self._db_connected:
            await self._connect_db()
        return self._pool  # pyright: ignore[reportReturnType]

    async def get_sensor_value(self, sensor_name: str) -> float | None:
        """Get latest sensor value from Redis or TimescaleDB fallback."""
        if self._sensor_repo:
            return await self._sensor_repo.get_sensor_value(sensor_name)
        raise RuntimeError("SensorRepository not initialized - call initialize() first")

    async def get_sensor_values_batch(self, sensor_names: list[str]) -> dict[str, float | None]:
        """Get latest sensor values for multiple sensors in a single batch query."""
        if self._sensor_repo:
            return await self._sensor_repo.get_sensor_values_batch(sensor_names)
        raise RuntimeError("SensorRepository not initialized - call initialize() first")

    async def get_device_state(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get device state from database."""
        if self._device_repo:
            return await self._device_repo.get_device_state(location, cluster, device_name)
        raise RuntimeError("DeviceRepository not initialized - call initialize() first")

    async def get_latest_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> float | None:
        """Get the most recent light intensity from automation_state table."""
        if self._device_repo:
            return await self._device_repo.get_latest_light_intensity(
                location, cluster, device_name
            )
        raise RuntimeError("DeviceRepository not initialized - call initialize() first")

    async def set_device_state(
        self, location: str, cluster: str, device_name: str, channel: int, state: int, mode: str
    ) -> bool:
        """Set device state in database and Redis state keys."""
        if self._device_repo:
            return await self._device_repo.set_device_state(
                location, cluster, device_name, channel, state, mode
            )
        raise RuntimeError("DeviceRepository not initialized - call initialize() first")

    async def log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        old_state: int | None,
        new_state: int | None,
        mode: str,
        reason: str,
        sensor_value: float | None = None,
        setpoint: float | None = None,
    ) -> bool:
        """Log control action to control_history."""
        if self._control_action_repo:
            return await self._control_action_repo.log_control_action(
                location,
                cluster,
                device_name,
                channel,
                old_state,
                new_state,
                mode,
                reason,
                sensor_value,
                setpoint,
            )
        raise RuntimeError("ControlActionRepository not initialized - call initialize() first")

    async def log_automation_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: float | None,
        duty_cycle_percent: float | None,
        active_rule_ids: list[int],
        active_schedule_ids: list[int],
        control_reason: str,
        schedule_ramp_up_duration: int | None = None,
        schedule_ramp_down_duration: int | None = None,
        schedule_photoperiod_hours: float | None = None,
        pid_kp: float | None = None,
        pid_ki: float | None = None,
        pid_kd: float | None = None,
    ) -> bool:
        """Log automation state to automation_state table, Redis Stream, and Redis state keys."""
        if self._control_action_repo:
            return await self._control_action_repo.log_automation_state(
                location,
                cluster,
                device_name,
                device_state,
                device_mode,
                pid_output,
                duty_cycle_percent,
                active_rule_ids,
                active_schedule_ids,
                control_reason,
                schedule_ramp_up_duration,
                schedule_ramp_down_duration,
                schedule_photoperiod_hours,
                pid_kp,
                pid_ki,
                pid_kd,
            )
        raise RuntimeError("ControlActionRepository not initialized - call initialize() first")

    async def get_setpoint(
        self, location: str, cluster: str, mode: str | None = None
    ) -> dict[str, Any] | None:
        """Get setpoints for location/cluster."""
        if self._setpoint_repo:
            return await self._setpoint_repo.get_setpoint(location, cluster, mode)
        raise RuntimeError("SetpointRepository not initialized - call initialize() first")

    async def set_setpoint(
        self,
        location: str,
        cluster: str,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        vpd: float | None = None,
        mode: str | None = None,
        ramp_in_duration: int | None = None,
        source: str = "api",
        expected_version: datetime | None = None,
    ) -> tuple[bool, datetime | None]:
        """Set setpoints for location/cluster."""
        if self._setpoint_repo:
            return await self._setpoint_repo.set_setpoint(
                location,
                cluster,
                heating_setpoint,
                cooling_setpoint,
                humidity,
                co2,
                vpd,
                mode,
                ramp_in_duration,
                source,
                expected_version,
            )
        raise RuntimeError("SetpointRepository not initialized - call initialize() first")

    async def log_effective_setpoint(
        self,
        location: str,
        cluster: str,
        mode: str,
        setpoint_type: str,
        raw_value: float,
        ramped_value: float,
        ramp_progress: float,
        source: str = "system",
    ) -> bool:
        """Log effective setpoint to effective_setpoints table."""
        if self._setpoint_repo:
            return await self._setpoint_repo.log_effective_setpoint(
                location,
                cluster,
                mode,
                setpoint_type,
                raw_value,
                ramped_value,
                ramp_progress,
                source,
            )
        raise RuntimeError("SetpointRepository not initialized - call initialize() first")

    async def log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        mode: str | None,
        effective_heating_setpoint: float | None = None,
        effective_cooling_setpoint: float | None = None,
        effective_humidity_setpoint: float | None = None,
        effective_co2_setpoint: float | None = None,
        effective_vpd_setpoint: float | None = None,
        nominal_heating_setpoint: float | None = None,
        nominal_cooling_setpoint: float | None = None,
        nominal_humidity_setpoint: float | None = None,
        nominal_co2_setpoint: float | None = None,
        nominal_vpd_setpoint: float | None = None,
        ramp_progress_heating: float | None = None,
        ramp_progress_cooling: float | None = None,
        ramp_progress_humidity: float | None = None,
        ramp_progress_co2: float | None = None,
        ramp_progress_vpd: float | None = None,
        device_name: str | None = None,
        effective_light_intensity: float | None = None,
        nominal_light_intensity: float | None = None,
        ramp_progress_light: float | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Log effective setpoints to effective_setpoints table using batching.

        This method buffers effective setpoint records and flushes them to database
        in batches every 10 seconds to reduce control loop latency. Records are
        still written to Redis immediately for real-time access.

        Args:
            location: Location name
            cluster: Cluster name
            mode: Current mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT) or None
            effective_heating_setpoint: Effective heating setpoint value (actual value being used)
            effective_cooling_setpoint: Effective cooling setpoint value (actual value being used)
            effective_humidity_setpoint: Effective humidity setpoint value (actual value being used)
            effective_co2_setpoint: Effective CO2 setpoint value (actual value being used)
            effective_vpd_setpoint: Effective VPD setpoint value (actual value being used)
            nominal_heating_setpoint: Nominal heating setpoint from database (reference value)
            nominal_cooling_setpoint: Nominal cooling setpoint from database (reference value)
            nominal_humidity_setpoint: Nominal humidity setpoint from database (reference value)
            nominal_co2_setpoint: Nominal CO2 setpoint from database (reference value)
            nominal_vpd_setpoint: Nominal VPD setpoint from database (reference value)
            ramp_progress_heating: Ramp progress for heating (0.0-1.0 or None if not ramping)
            ramp_progress_cooling: Ramp progress for cooling (0.0-1.0 or None if not ramping)
            ramp_progress_humidity: Ramp progress for humidity (0.0-1.0 or None if not ramping)
            ramp_progress_co2: Ramp progress for CO2 (0.0-1.0 or None if not ramping)
            ramp_progress_vpd: Ramp progress for VPD (0.0-1.0 or None if not ramping)
            device_name: Device name for per-device logging (e.g., light_1, light_2)
            effective_light_intensity: Effective light intensity (0-100%) after ramp
            nominal_light_intensity: Nominal/target light intensity from schedule
            ramp_progress_light: Ramp progress for light (0.0-1.0 or None if not ramping)
            timestamp: Optional timestamp (defaults to NOW())

        Returns:
            True if buffered successfully, False otherwise
        """
        try:
            ts = timestamp or datetime.now()
            db_mode = mode if mode else None

            # Buffer the record for batch writing (performance optimization)
            record = {
                "timestamp": ts,
                "location": location,
                "cluster": cluster,
                "mode": db_mode,
                "device_name": device_name,
                "effective_heating_setpoint": effective_heating_setpoint,
                "effective_cooling_setpoint": effective_cooling_setpoint,
                "effective_humidity_setpoint": effective_humidity_setpoint,
                "effective_co2_setpoint": effective_co2_setpoint,
                "effective_vpd_setpoint": effective_vpd_setpoint,
                "effective_light_intensity": effective_light_intensity,
                "nominal_heating_setpoint": nominal_heating_setpoint,
                "nominal_cooling_setpoint": nominal_cooling_setpoint,
                "nominal_humidity_setpoint": nominal_humidity_setpoint,
                "nominal_co2_setpoint": nominal_co2_setpoint,
                "nominal_vpd_setpoint": nominal_vpd_setpoint,
                "nominal_light_intensity": nominal_light_intensity,
                "ramp_progress_heating": ramp_progress_heating,
                "ramp_progress_cooling": ramp_progress_cooling,
                "ramp_progress_humidity": ramp_progress_humidity,
                "ramp_progress_co2": ramp_progress_co2,
                "ramp_progress_vpd": ramp_progress_vpd,
                "ramp_progress_light": ramp_progress_light,
            }

            self._batch_buffer.append(record)

            # Check if it's time to flush the batch
            current_time = time.time()
            if current_time - self._last_batch_flush >= self._batch_interval:
                await self.flush_batch_buffer()

            # Write effective setpoints to Redis immediately for real-time access
            # State keys = fast truth for automation, Streams = history for dashboards/DB
            if self._automation_redis and self._automation_redis.redis_enabled:
                self._automation_redis.write_effective_setpoints(
                    location=location,
                    cluster=cluster,
                    effective_heating_setpoint=effective_heating_setpoint,
                    effective_cooling_setpoint=effective_cooling_setpoint,
                    effective_humidity_setpoint=effective_humidity_setpoint,
                    effective_co2_setpoint=effective_co2_setpoint,
                    effective_vpd_setpoint=effective_vpd_setpoint,
                    device_name=device_name,
                    effective_light_intensity=effective_light_intensity,
                    nominal_heating_setpoint=nominal_heating_setpoint,
                    nominal_cooling_setpoint=nominal_cooling_setpoint,
                    nominal_humidity_setpoint=nominal_humidity_setpoint,
                    nominal_co2_setpoint=nominal_co2_setpoint,
                    nominal_vpd_setpoint=nominal_vpd_setpoint,
                    ramp_progress_heating=ramp_progress_heating,
                    ramp_progress_cooling=ramp_progress_cooling,
                    ramp_progress_humidity=ramp_progress_humidity,
                    ramp_progress_co2=ramp_progress_co2,
                    ramp_progress_vpd=ramp_progress_vpd,
                    mode=mode,
                )

            return True
        except Exception as e:
            logger.error(f"Error buffering effective setpoints: {e}")
            return False

    async def get_all_setpoints_for_location_cluster(
        self, location: str, cluster: str
    ) -> list[dict[str, Any]]:
        """Get all setpoints for a location/cluster."""
        if self._setpoint_repo:
            return await self._setpoint_repo.get_all_setpoints_for_location_cluster(
                location, cluster
            )
        raise RuntimeError("SetpointRepository not initialized - call initialize() first")

    async def get_latest_effective_setpoints(
        self, location: str, cluster: str
    ) -> dict[str, Any] | None:
        """Get latest effective setpoints for location/cluster."""
        if self._setpoint_repo:
            return await self._setpoint_repo.get_latest_effective_setpoints(location, cluster)
        raise RuntimeError("SetpointRepository not initialized - call initialize() first")

    async def set_device_mapping(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        active_high: bool = True,
        safe_state: bool = False,
        mcp_board_id: int = 0,
    ) -> bool:
        """Set device hardware mapping."""
        if self._device_repo:
            return await self._device_repo.set_device_mapping(
                location, cluster, device_name, channel, active_high, safe_state, mcp_board_id
            )
        raise RuntimeError("DeviceRepository not initialized - call initialize() first")

    async def get_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Get PID parameters from database."""
        if self._pid_repo:
            return await self._pid_repo.get_pid_parameters(device_type)
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def set_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = "manual",
        updated_by: str = "system",
    ) -> bool:
        """Set PID parameters for a device type."""
        if self._pid_repo:
            return await self._pid_repo.set_pid_parameters(
                device_type, kp, ki, kd, source, updated_by
            )
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def get_pid_parameter_history(
        self, device_type: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get PID parameter history."""
        if self._pid_repo:
            return await self._pid_repo.get_pid_parameter_history(device_type, limit)
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def get_all_pid_parameters(self) -> list[dict[str, Any]]:
        """Get all PID parameters."""
        if self._pid_repo:
            return await self._pid_repo.get_all_pid_parameters()
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def get_pid_control_mode(self, device_type: str) -> dict | None:
        """Get PID control mode for a device type."""
        if self._pid_repo:
            return await self._pid_repo.get_pid_control_mode(device_type)
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def set_pid_control_mode(
        self,
        device_type: str,
        mode: str,
        hysteresis_high: float | None = None,
        hysteresis_low: float | None = None,
        updated_by: str = "system",
    ) -> bool:
        """Set PID control mode for a device type."""
        if self._pid_repo:
            return await self._pid_repo.set_pid_control_mode(
                device_type, mode, hysteresis_high, hysteresis_low, updated_by
            )
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def get_autotune_state(self, device_type: str) -> dict[str, Any] | None:
        """Get autotune state for a device type."""
        if self._pid_repo:
            return await self._pid_repo.get_autotune_state(device_type)
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def update_autotune_state(
        self,
        device_type: str,
        state: str,
        progress: float | None = None,
        current_step: str | None = None,
        error_message: str | None = None,
        result_kp: float | None = None,
        result_ki: float | None = None,
        result_kd: float | None = None,
    ) -> bool:
        """Update autotune state for a device type."""
        if self._pid_repo:
            return await self._pid_repo.update_autotune_state(
                device_type,
                state,
                progress,
                current_step,
                error_message,
                result_kp,
                result_ki,
                result_kd,
            )
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def set_pid_parameters_with_reason(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        change_reason: str,
        source: str = "auto_pid",
        updated_by: str | None = None,
    ) -> bool:
        """Set PID parameters with a reason for the change."""
        if self._pid_repo:
            return await self._pid_repo.set_pid_parameters_with_reason(
                device_type, kp, ki, kd, change_reason, source, updated_by
            )
        raise RuntimeError("PIDRepository not initialized - call initialize() first")

    async def get_schedules(
        self,
        location: str | None = None,
        cluster: str | None = None,
        device_name: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get schedules with optional filters."""
        if self._schedule_repo:
            # Repository only supports location/cluster filtering
            # device_name and enabled_only filtering done in-memory if needed
            schedules = await self._schedule_repo.get_schedules(location, cluster)
            if device_name:
                schedules = [s for s in schedules if s.get("device_name") == device_name]
            if enabled_only:
                schedules = [s for s in schedules if s.get("enabled")]
            return schedules
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def get_climate_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get climate schedule data (pre-day/pre-night durations) for a location/cluster."""
        if self._schedule_repo:
            return await self._schedule_repo.get_climate_schedule(location, cluster)
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def get_light_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get light schedule (day start/end times) for a location/cluster."""
        if self._schedule_repo:
            return await self._schedule_repo.get_room_light_schedule(location, cluster)
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def create_schedule(
        self,
        name: str,
        location: str,
        cluster: str,
        device_name: str,
        start_time: dt_time,
        end_time: dt_time,
        day_of_week: int | None = None,
        enabled: bool = True,
        mode: str | None = None,
        target_intensity: float | None = None,
        ramp_up_duration: int = 0,
        ramp_down_duration: int = 0,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        """Create a new schedule."""
        if self._schedule_repo:
            # Convert time objects to strings for repository
            start_str = (
                start_time.strftime("%H:%M") if hasattr(start_time, "strftime") else str(start_time)
            )
            end_str = end_time.strftime("%H:%M") if hasattr(end_time, "strftime") else str(end_time)
            return await self._schedule_repo.create_schedule(
                name,
                location,
                cluster,
                device_name,
                start_str,
                end_str,
                day_of_week,
                enabled,
                mode,
                target_intensity,
                ramp_up_duration,
                ramp_down_duration,
                conn,
            )
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def update_schedule(
        self,
        schedule_id: int,
        name: str | None = None,
        start_time: dt_time | None = None,
        end_time: dt_time | None = None,
        day_of_week: int | None = None,
        enabled: bool | None = None,
        mode: str | None = None,
        target_intensity: float | None = None,
        ramp_up_duration: int | None = None,
        ramp_down_duration: int | None = None,
    ) -> bool:
        """Update an existing schedule."""
        if self._schedule_repo:
            return await self._schedule_repo.update_schedule(
                schedule_id,
                name,
                start_time,
                end_time,
                day_of_week,
                enabled,
                mode,
                target_intensity,
                ramp_up_duration,
                ramp_down_duration,
            )
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def delete_schedule(self, schedule_id: int) -> bool:
        """Delete a schedule by ID."""
        if self._schedule_repo:
            return await self._schedule_repo.delete_schedule(schedule_id)
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def delete_schedules_bulk(self, schedule_ids: list[int]) -> int:
        """Delete multiple schedules by IDs."""
        if self._schedule_repo:
            return await self._schedule_repo.delete_schedules_bulk(schedule_ids)
        raise RuntimeError("ScheduleRepository not initialized - call initialize() first")

    async def load_schedule_state_to_redis(self) -> None:
        """Load all schedule state from database to Redis following canonical schema.

        Queries all room schedules, climate schedules, setpoints (including PRE_DAY and PRE_NIGHT),
        and light schedules from DB, groups by location/cluster, and writes to Redis state.
        Called on service startup to populate Redis with current schedule configuration.
        """
        if not self._automation_redis or not self._automation_redis.redis_enabled:
            logger.warning("Redis not enabled, skipping schedule state load")
            return

        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Get all unique location/cluster pairs
                rows = await conn.fetch("""
                    SELECT DISTINCT location, cluster
                    FROM schedules
                    UNION
                    SELECT DISTINCT location, cluster
                    FROM setpoints
                """)

                locations_loaded = []

                for row in rows:
                    location = row["location"]
                    cluster = row["cluster"]

                    try:
                        # Build schedule state using the helper function from schedules.py
                        # Import here to avoid circular dependency
                        from .routes.schedules import _build_schedule_state

                        schedule_state = await _build_schedule_state(self, location, cluster)

                        # Write to Redis
                        self._automation_redis.write_schedule_state(
                            location, cluster, schedule_state
                        )
                        locations_loaded.append(f"{location}/{cluster}")
                        logger.debug(f"Loaded schedule state to Redis for {location}/{cluster}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to load schedule state for {location}/{cluster}: {e}"
                        )

                if locations_loaded:
                    logger.info(
                        f"Loaded schedule state to Redis for {len(locations_loaded)} locations: {', '.join(locations_loaded)}"
                    )
                else:
                    logger.info("No schedule state to load (no locations found in database)")
        except Exception as e:
            logger.error(f"Error loading schedule state to Redis: {e}", exc_info=True)

    async def close(self):
        """Close database connections."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            self._db_connected = False
        if self._redis_client:
            self._redis_client.close()
            self._redis_client = None
            self._redis_enabled = False

    async def _create_room_modes_tables(self) -> None:
        """Create room modes tables for the new UI."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Room modes table (Veg, Flower, Drying, Sleep)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS room_modes (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    photoperiod_hours INTEGER CHECK (photoperiod_hours >= 0 AND photoperiod_hours <= 24),
                    is_constant BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Insert default modes if not exist
            await conn.execute("""
                INSERT INTO room_modes (name, description, photoperiod_hours, is_constant)
                VALUES 
                    ('veg', 'Vegetative growth - 18/6 photoperiod', 18, FALSE),
                    ('flower', 'Flowering - 12/12 photoperiod', 12, FALSE),
                    ('drying', 'Drying - 24h constant conditions', 0, TRUE),
                    ('sleep', 'Sleep mode - minimal energy', 0, TRUE)
                ON CONFLICT (name) DO NOTHING
            """)

            # Flower submodes table (Stretch, Bulk, Ripen)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS flower_submodes (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    week_start INTEGER CHECK (week_start >= 1),
                    week_end INTEGER CHECK (week_end >= 1),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Insert default flower submodes
            await conn.execute("""
                INSERT INTO flower_submodes (name, description, week_start, week_end)
                VALUES 
                    ('stretch', 'Stretch phase - weeks 1-3', 1, 3),
                    ('bulk', 'Bulk phase - weeks 4-6', 4, 6),
                    ('ripen', 'Ripen phase - weeks 7-9', 7, 9)
                ON CONFLICT (name) DO NOTHING
            """)

            # Room active mode table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS room_active_mode (
                    id SERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    mode_id INTEGER REFERENCES room_modes(id),
                    submode_id INTEGER REFERENCES flower_submodes(id),
                    activated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(location, cluster)
                )
            """)

            # Light presets per mode
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS light_presets (
                    id SERIAL PRIMARY KEY,
                    mode_id INTEGER REFERENCES room_modes(id),
                    submode_id INTEGER REFERENCES flower_submodes(id),
                    lights_on_hour INTEGER CHECK (lights_on_hour >= 0 AND lights_on_hour < 24),
                    lights_off_hour INTEGER CHECK (lights_off_hour >= 0 AND lights_off_hour < 24),
                    intensity_day INTEGER CHECK (intensity_day >= 0 AND intensity_day <= 100),
                    intensity_night INTEGER CHECK (intensity_night >= 0 AND intensity_night <= 100),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Mode parameters table - stores ALL parameters per room/mode/submode combination
            # Each mode/submode has its own saved parameters that persist through mode switches
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mode_parameters (
                    id SERIAL PRIMARY KEY,
                    location TEXT NOT NULL,
                    cluster TEXT NOT NULL,
                    mode_id INTEGER REFERENCES room_modes(id) NOT NULL,
                    submode_id INTEGER REFERENCES flower_submodes(id),  -- NULL for non-Flower modes
                    
                    -- Schedule parameters
                    day_start_time TIME NOT NULL DEFAULT '17:00',
                    night_start_time TIME NOT NULL DEFAULT '11:00',
                    ramp_up_minutes INTEGER NOT NULL DEFAULT 30,
                    ramp_down_minutes INTEGER NOT NULL DEFAULT 30,
                    pre_day_minutes INTEGER NOT NULL DEFAULT 30,
                    pre_night_minutes INTEGER NOT NULL DEFAULT 30,
                    light_ramp_up_minutes INTEGER NOT NULL DEFAULT 15,
                    light_ramp_down_minutes INTEGER NOT NULL DEFAULT 15,
                    
                    -- Pre-Day setpoints
                    pre_day_heat_temp REAL NOT NULL DEFAULT 22.0,
                    pre_day_cool_temp REAL NOT NULL DEFAULT 26.0,
                    pre_day_vpd REAL NOT NULL DEFAULT 0.9,
                    pre_day_co2 INTEGER NOT NULL DEFAULT 700,
                    
                    -- Day setpoints
                    day_heat_temp REAL NOT NULL DEFAULT 24.0,
                    day_cool_temp REAL NOT NULL DEFAULT 28.0,
                    day_vpd REAL NOT NULL DEFAULT 1.0,
                    day_co2 INTEGER NOT NULL DEFAULT 800,
                    day_leaf_delta REAL NOT NULL DEFAULT -2.0,
                    
                    -- Pre-Night setpoints
                    pre_night_heat_temp REAL NOT NULL DEFAULT 22.0,
                    pre_night_cool_temp REAL NOT NULL DEFAULT 26.0,
                    pre_night_vpd REAL NOT NULL DEFAULT 0.9,
                    pre_night_co2 INTEGER NOT NULL DEFAULT 700,
                    
                    -- Night setpoints
                    night_heat_temp REAL NOT NULL DEFAULT 20.0,
                    night_cool_temp REAL NOT NULL DEFAULT 24.0,
                    night_vpd REAL NOT NULL DEFAULT 0.8,
                    night_co2 INTEGER NOT NULL DEFAULT 600,
                    night_leaf_delta REAL NOT NULL DEFAULT -1.0,
                    
                    -- Light intensity (percentage)
                    main_light_intensity INTEGER NOT NULL DEFAULT 100,
                    supplemental_light_intensity INTEGER NOT NULL DEFAULT 0,
                    
                    -- Timestamps
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    
                    -- Unique constraint: one parameter set per room/mode/submode
                    UNIQUE(location, cluster, mode_id, submode_id)
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mode_parameters_lookup
                ON mode_parameters(location, cluster, mode_id, submode_id)
            """)

            logger.info("Room modes tables created/verified")

    async def get_room_modes(self) -> list[dict[str, Any]]:
        """Get all room modes."""
        if self._room_mode_repo:
            return await self._room_mode_repo.get_room_modes()
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        """Get all flower submodes."""
        if self._room_mode_repo:
            return await self._room_mode_repo.get_flower_submodes()
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def get_active_mode(self, location: str, cluster: str) -> dict | None:
        """Get active mode for a location/cluster."""
        if self._room_mode_repo:
            return await self._room_mode_repo.get_active_mode(location, cluster)
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def set_active_mode(
        self, location: str, cluster: str, mode_name: str, submode_name: str | None = None
    ) -> bool:
        """Set active mode for a location/cluster."""
        if self._room_mode_repo:
            return await self._room_mode_repo.set_active_mode(
                location, cluster, mode_name, submode_name
            )
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def get_mode_parameters(
        self, location: str, cluster: str, mode_name: str, submode_name: str | None = None
    ) -> dict[str, Any] | None:
        """Get mode parameters for a location/cluster/mode."""
        if self._room_mode_repo:
            return await self._room_mode_repo.get_mode_parameters(
                location, cluster, mode_name, submode_name
            )
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def save_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, Any],
    ) -> bool:
        """Save mode parameters."""
        if self._room_mode_repo:
            return await self._room_mode_repo.save_mode_parameters(
                location, cluster, mode_name, submode_name, params
            )
        raise RuntimeError("RoomModeRepository not initialized - call initialize() first")

    async def update_light_schedule_target(
        self, location: str, cluster: str, device_name: str, target_intensity: float
    ) -> bool:
        """Update target_intensity for a light device's active schedule."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE schedules 
                SET target_intensity = $4, updated_at = NOW()
                WHERE location = $1 AND cluster = $2 AND device_name = $3
                AND enabled = true AND target_intensity IS NOT NULL
            """,
                location,
                cluster,
                device_name,
                target_intensity,
            )
            return result != "UPDATE 0"

    async def update_light_schedule_times(
        self, location: str, cluster: str, device_name: str, start_time: str, end_time: str
    ) -> bool:
        """Update start/end times for a light device's day schedule."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE schedules 
                SET start_time = $4::time, end_time = $5::time, updated_at = NOW()
                WHERE location = $1 AND cluster = $2 AND device_name = $3
                AND enabled = true AND target_intensity IS NOT NULL
                AND target_intensity > 0
            """,
                location,
                cluster,
                device_name,
                start_time,
                end_time,
            )
            return result != "UPDATE 0"
