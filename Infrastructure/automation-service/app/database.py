"""Database manager for TimescaleDB operations."""
import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncpg
import redis
from app.redis_client import AutomationRedisClient

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages TimescaleDB database connections and operations for automation service."""
    
    def __init__(self, db_config: Optional[Dict[str, Any]] = None, redis_url: Optional[str] = None):
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
                "port": int(os.getenv("POSTGRES_PORT", "5432"))
            }
        else:
            self.db_config = db_config
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._pool: Optional[asyncpg.Pool] = None
        self._redis_client: Optional[redis.Redis] = None
        self._redis_enabled = False
        self._automation_redis: Optional[AutomationRedisClient] = None
        self._db_connected = False
        self._retry_delay = 1.0  # Initial retry delay in seconds
        self._max_retry_delay = 60.0  # Maximum retry delay
    
    async def initialize(self) -> bool:
        """Initialize database connection and create tables.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            await self._connect_db()
            await self._create_tables()
            await self._connect_redis()
            # Initialize automation Redis client for stream and state writes
            self._automation_redis = AutomationRedisClient(redis_url=self.redis_url, redis_ttl=10)
            self._automation_redis.connect()
            return True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            return False
    
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
                    server_settings={
                        'application_name': 'automation_service'
                    }
                )
                self._db_connected = True
                self._retry_delay = 1.0  # Reset retry delay on success
                logger.info("Connected to TimescaleDB")
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = min(self._retry_delay * (2 ** attempt), self._max_retry_delay)
                    logger.warning(f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ConnectionError(f"Failed to connect to TimescaleDB after {max_retries} attempts: {e}")
    
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
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                logger.warning("TimescaleDB extension not available for automation_state, using regular table")
            
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
                    effective_heating_setpoint REAL,
                    effective_cooling_setpoint REAL,
                    effective_humidity_setpoint REAL,
                    effective_co2_setpoint REAL,
                    effective_vpd_setpoint REAL,
                    nominal_heating_setpoint REAL,
                    nominal_cooling_setpoint REAL,
                    nominal_humidity_setpoint REAL,
                    nominal_co2_setpoint REAL,
                    nominal_vpd_setpoint REAL,
                    ramp_progress_heating REAL,
                    ramp_progress_cooling REAL,
                    ramp_progress_humidity REAL,
                    ramp_progress_co2 REAL,
                    ramp_progress_vpd REAL
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('effective_setpoints', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning("TimescaleDB extension not available for effective_setpoints, using regular table")
            
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
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location 
                ON effective_setpoints(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp 
                ON effective_setpoints(timestamp DESC)
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
        author: Optional[str] = None,
        comment: Optional[str] = None,
        location: Optional[str] = None,
        cluster: Optional[str] = None,
        changes: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
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
                row = await conn.fetchrow("""
                    INSERT INTO config_versions 
                    (timestamp, author, comment, config_type, location, cluster, changes)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6)
                    RETURNING version_id
                """, author, comment, config_type, location, cluster, 
                    json.dumps(changes) if changes else None)
                return row['version_id'] if row else None
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
                logger.warning("TimescaleDB extension not available for setpoint_history, using regular table")
            
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
                logger.warning(f"Migration check for temperature column failed (second location): {e}")
            
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
                    logger.info("Migrated temperature column to heating_setpoint in setpoint_history")
            except Exception as e:
                logger.warning(f"Migration check for temperature column in setpoint_history failed: {e}")
            
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
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                logger.warning("TimescaleDB extension not available for automation_state, using regular table")
            
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
                    effective_heating_setpoint REAL,
                    effective_cooling_setpoint REAL,
                    effective_humidity_setpoint REAL,
                    effective_co2_setpoint REAL,
                    effective_vpd_setpoint REAL,
                    nominal_heating_setpoint REAL,
                    nominal_cooling_setpoint REAL,
                    nominal_humidity_setpoint REAL,
                    nominal_co2_setpoint REAL,
                    nominal_vpd_setpoint REAL,
                    ramp_progress_heating REAL,
                    ramp_progress_cooling REAL,
                    ramp_progress_humidity REAL,
                    ramp_progress_co2 REAL,
                    ramp_progress_vpd REAL
                )
            """)
            # Create hypertable if TimescaleDB extension is available
            try:
                await conn.execute("""
                    SELECT create_hypertable('effective_setpoints', 'timestamp', if_not_exists => TRUE)
                """)
            except Exception:
                logger.warning("TimescaleDB extension not available for effective_setpoints, using regular table")
            
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
            
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location 
                ON effective_setpoints(location, cluster)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp 
                ON effective_setpoints(timestamp DESC)
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
        return self._pool
    
    async def get_sensor_value(self, sensor_name: str) -> Optional[float]:
        """Get latest sensor value from Redis or TimescaleDB fallback.
        
        Args:
            sensor_name: Sensor name (e.g., 'dry_bulb_f', 'rh_b', 'co2_f')
        
        Returns:
            Sensor value as float, or None if not found
        """
        # Try Redis first
        if self._redis_enabled and self._redis_client:
            try:
                value = self._redis_client.get(f"sensor:{sensor_name}")
                if value is not None:
                    try:
                        return float(value)
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.debug(f"Redis read failed for {sensor_name}: {e}")
                # Try to reconnect
                try:
                    await self._connect_redis()
                except Exception:
                    pass
        
        # Fallback to TimescaleDB (using measurement table)
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Query measurement table directly using sensor name
                row = await conn.fetchrow("""
                    SELECT m.value
                    FROM measurement m
                    JOIN sensor s ON m.sensor_id = s.sensor_id
                    WHERE s.name = $1
                    ORDER BY m.time DESC
                    LIMIT 1
                """, sensor_name)
                
                if row and row['value'] is not None:
                    try:
                        return float(row['value'])
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.error(f"Error reading sensor {sensor_name} from TimescaleDB: {e}")
        
        return None
    
    
    async def get_device_state(self, location: str, cluster: str, device_name: str) -> Optional[Dict[str, Any]]:
        """Get device state from database."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT state, mode, channel, updated_at
                    FROM device_states
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                """, location, cluster, device_name)
                
                if row:
                    return {
                        'state': row['state'],
                        'mode': row['mode'],
                        'channel': row['channel'],
                        'updated_at': row['updated_at']
                    }
        except Exception as e:
            logger.error(f"Error getting device state: {e}")
        return None
    
    async def get_latest_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> Optional[float]:
        """Get the most recent light intensity from automation_state table.
        
        For lights, the intensity is stored in duty_cycle_percent field.
        This is more reliable than Redis since it persists across restarts.
        
        Returns:
            Light intensity (0-100%) or None if not found
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT duty_cycle_percent, timestamp
                    FROM automation_state
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                      AND duty_cycle_percent IS NOT NULL
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, location, cluster, device_name)
                
                if row and row['duty_cycle_percent'] is not None:
                    return float(row['duty_cycle_percent'])
        except Exception as e:
            logger.debug(f"Error getting latest light intensity from database: {e}")
        return None
    
    async def set_device_state(
        self, 
        location: str, 
        cluster: str, 
        device_name: str, 
        channel: int,
        state: int, 
        mode: str
    ) -> bool:
        """Set device state in database and Redis state keys."""
        # Write to TimescaleDB
        db_success = False
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO device_states (location, cluster, device_name, channel, state, mode, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT (location, cluster, device_name)
                    DO UPDATE SET state = EXCLUDED.state, mode = EXCLUDED.mode, 
                                  channel = EXCLUDED.channel, updated_at = NOW()
                """, location, cluster, device_name, channel, state, mode)
                db_success = True
        except Exception as e:
            logger.error(f"Error setting device state: {e}")
        
        # Write to Redis state keys (for live device state)
        if self._automation_redis and self._automation_redis.redis_enabled:
            self._automation_redis.write_to_state(
                location, cluster, device_name, state, mode
            )
        
        return db_success
    
    async def log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        old_state: Optional[int],
        new_state: Optional[int],
        mode: str,
        reason: str,
        sensor_value: Optional[float] = None,
        setpoint: Optional[float] = None
    ) -> bool:
        """Log control action to control_history."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO control_history 
                    (timestamp, location, cluster, device_name, channel, old_state, new_state, 
                     mode, reason, sensor_value, setpoint)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """, location, cluster, device_name, channel, old_state, new_state, 
                    mode, reason, sensor_value, setpoint)
                return True
        except Exception as e:
            logger.error(f"Error logging control action: {e}")
            return False
    
    async def log_automation_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: int,
        device_mode: str,
        pid_output: Optional[float],
        duty_cycle_percent: Optional[float],
        active_rule_ids: List[int],
        active_schedule_ids: List[int],
        control_reason: str,
        schedule_ramp_up_duration: Optional[int] = None,
        schedule_ramp_down_duration: Optional[int] = None,
        schedule_photoperiod_hours: Optional[float] = None,
        pid_kp: Optional[float] = None,
        pid_ki: Optional[float] = None,
        pid_kd: Optional[float] = None
    ) -> bool:
        """Log automation state to automation_state table, Redis Stream, and Redis state keys."""
        # Write to TimescaleDB
        db_success = False
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO automation_state 
                    (timestamp, location, cluster, device_name, device_state, device_mode,
                     pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids, 
                     control_reason, schedule_ramp_up_duration, schedule_ramp_down_duration,
                     schedule_photoperiod_hours, pid_kp, pid_ki, pid_kd, updated_at)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, NOW())
                """, location, cluster, device_name, device_state, device_mode,
                    pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids, control_reason,
                    schedule_ramp_up_duration, schedule_ramp_down_duration, schedule_photoperiod_hours,
                    pid_kp, pid_ki, pid_kd)
                db_success = True
        except Exception as e:
            logger.error(f"Error logging automation state to database: {e}")
        
        # Write to Redis Stream and state keys
        if self._automation_redis and self._automation_redis.redis_enabled:
            # Write to stream
            self._automation_redis.write_to_stream(
                location, cluster, device_name, device_state, device_mode,
                pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids, control_reason
            )
            # Write to state keys
            self._automation_redis.write_to_state(
                location, cluster, device_name, device_state, device_mode,
                pid_output, duty_cycle_percent
            )
        
        return db_success
    
    async def get_setpoint(self, location: str, cluster: str, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get setpoints for location/cluster.
        
        Reads from Redis first (fast), falls back to database if Redis unavailable or TTL expired.
        If found in database, caches in Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            mode: Mode (DAY/NIGHT/TRANSITION) or None for legacy/default setpoint
        
        Returns:
            Dict with setpoint values including mode and vpd, or None if not found
        """
        # Normalize mode: None becomes NULL in database (legacy behavior)
        db_mode = mode if mode else None
        
        # Try Redis first (Redis doesn't support mode yet, so only for legacy mode=NULL)
        if db_mode is None and self._automation_redis and self._automation_redis.redis_enabled:
            redis_setpoint = self._automation_redis.read_setpoint(location, cluster)
            if redis_setpoint:
                # Check if we have all required values
                if 'heating_setpoint' in redis_setpoint or 'cooling_setpoint' in redis_setpoint or 'humidity' in redis_setpoint or 'co2' in redis_setpoint:
                    # Return what we have from Redis (may be partial if TTL expired on some keys)
                    return {
                        'heating_setpoint': redis_setpoint.get('heating_setpoint'),
                        'cooling_setpoint': redis_setpoint.get('cooling_setpoint'),
                        'humidity': redis_setpoint.get('humidity'),
                        'co2': redis_setpoint.get('co2'),
                        'vpd': redis_setpoint.get('vpd'),
                        'mode': None
                    }
        
        # Fallback to database (Redis unavailable, TTL expired, or mode-based setpoint)
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT heating_setpoint, cooling_setpoint, humidity, co2, vpd, mode, ramp_in_duration
                    FROM setpoints
                    WHERE location = $1 AND cluster = $2 AND (mode = $3 OR (mode IS NULL AND $3 IS NULL))
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, location, cluster, db_mode)
                
                if row:
                    setpoint_data = {
                        'heating_setpoint': row['heating_setpoint'],
                        'cooling_setpoint': row['cooling_setpoint'],
                        'humidity': row['humidity'],
                        'co2': row['co2'],
                        'vpd': row['vpd'],
                        'mode': row['mode'],
                        'ramp_in_duration': row['ramp_in_duration']
                    }
                    
                    # Note: User-set (nominal) setpoints are NOT cached in Redis
                    # Only effective setpoints are written to Redis (updated every control step)
                    
                    return setpoint_data
        except Exception as e:
            logger.error(f"Error getting setpoint: {e}")
        return None
    
    async def set_setpoint(
        self, 
        location: str, 
        cluster: str, 
        heating_setpoint: Optional[float] = None,
        cooling_setpoint: Optional[float] = None,
        humidity: Optional[float] = None,
        co2: Optional[float] = None,
        vpd: Optional[float] = None,
        mode: Optional[str] = None,
        ramp_in_duration: Optional[int] = None,
        source: str = 'api'
    ) -> bool:
        """Set setpoints for location/cluster.
        
        Validates setpoints, then writes to database only.
        Note: User-set (nominal) setpoints are NOT written to Redis.
        Only effective setpoints (computed at runtime) are written to Redis.
        
        Args:
            location: Location name
            cluster: Cluster name
            heating_setpoint: Heating setpoint (optional)
            cooling_setpoint: Cooling setpoint (optional)
            humidity: Humidity setpoint (optional)
            co2: CO2 setpoint (optional)
            vpd: VPD setpoint (optional)
            mode: Mode (DAY/NIGHT/TRANSITION) or None for legacy/default setpoint
            source: Source of setpoint ('api', 'schedule', 'failsafe', 'cli')
        
        Returns:
            True if successful, False otherwise
        """
        # Import validation here to avoid circular imports
        from app.validation import validate_setpoint
        from app.config import ConfigLoader
        
        # Validate setpoints if provided
        # Note: We need config for validation, but we'll do basic validation here
        # Full validation should be done in the API endpoint before calling this
        
        # Normalize mode: None becomes NULL in database (legacy behavior)
        db_mode = mode if mode else None
        
        existing = None
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Get latest existing setpoints for this mode (or mode=NULL for legacy) within transaction
                    row = await conn.fetchrow("""
                        SELECT heating_setpoint, cooling_setpoint, humidity, co2, vpd, mode, ramp_in_duration
                        FROM setpoints
                        WHERE location = $1 AND cluster = $2 AND (mode = $3 OR (mode IS NULL AND $3 IS NULL))
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """, location, cluster, db_mode)
                    
                    if row:
                        existing = {
                            'heating_setpoint': row['heating_setpoint'],
                            'cooling_setpoint': row['cooling_setpoint'],
                            'humidity': row['humidity'],
                            'co2': row['co2'],
                            'vpd': row['vpd'],
                            'mode': row['mode'],
                            'ramp_in_duration': row['ramp_in_duration']
                        }
                    
                    # Merge incoming values with latest existing so we always insert a complete row
                    heat = heating_setpoint if heating_setpoint is not None else (existing.get('heating_setpoint') if existing else None)
                    cool = cooling_setpoint if cooling_setpoint is not None else (existing.get('cooling_setpoint') if existing else None)
                    hum = humidity if humidity is not None else (existing.get('humidity') if existing else None)
                    co2_val = co2 if co2 is not None else (existing.get('co2') if existing else None)
                    vpd_val = vpd if vpd is not None else (existing.get('vpd') if existing else None)
                    ramp_in = ramp_in_duration if ramp_in_duration is not None else (existing.get('ramp_in_duration') if existing else None)
                    
                    # Insert a new row to preserve history (no overwrite)
                    await conn.execute("""
                        INSERT INTO setpoints (location, cluster, heating_setpoint, cooling_setpoint, humidity, co2, vpd, mode, ramp_in_duration, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                    """, location, cluster, heat, cool, hum, co2_val, vpd_val, db_mode, ramp_in)
                    
                    # Log to setpoint_history for time-series queries (Grafana)
                    await conn.execute("""
                        INSERT INTO setpoint_history (timestamp, location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                        VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8)
                    """, location, cluster, db_mode, heat, cool, hum, co2_val, vpd_val)
                
                # Note: User-set (nominal) setpoints are NOT written to Redis
                # Only effective setpoints (computed at runtime) are written to Redis
                
                return True
        except asyncpg.PostgresConnectionError as e:
            logger.error(f"Database connection error setting setpoint: {e}")
            return False
        except asyncpg.PostgresError as e:
            logger.error(f"Database error setting setpoint: {e}")
            return False
        except Exception as e:
            logger.error(f"Error setting setpoint: {e}", exc_info=True)
            return False
    
    async def log_effective_setpoint(
        self,
        location: str,
        cluster: str,
        mode: Optional[str],
        heating_setpoint: Optional[float] = None,
        cooling_setpoint: Optional[float] = None,
        humidity: Optional[float] = None,
        co2: Optional[float] = None,
        vpd: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> bool:
        """Log effective setpoint to setpoint_history (for ramp tracking).
        
        .. deprecated:: Use log_effective_setpoints (plural) instead.
        
        This is called during ramps to log the effective setpoint at each change.
        This method is deprecated in favor of log_effective_setpoints which logs
        both heating and cooling setpoints together at the location/cluster level.
        
        Args:
            location: Location name
            cluster: Cluster name
            mode: Mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT/TRANSITION) or None
            heating_setpoint: Effective heating setpoint
            cooling_setpoint: Effective cooling setpoint
            humidity: Effective humidity setpoint
            co2: Effective CO2 setpoint
            vpd: Effective VPD setpoint
            timestamp: Timestamp (default: NOW())
        
        Returns:
            True if successful, False otherwise
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                db_mode = mode if mode else None
                ts = timestamp or datetime.now()
                
                await conn.execute("""
                    INSERT INTO setpoint_history (timestamp, location, cluster, mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """, ts, location, cluster, db_mode, heating_setpoint, cooling_setpoint, humidity, co2, vpd)
                
                return True
        except Exception as e:
            logger.error(f"Error logging effective setpoint: {e}")
            return False
    
    async def log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        mode: Optional[str],
        effective_heating_setpoint: Optional[float] = None,
        effective_cooling_setpoint: Optional[float] = None,
        effective_humidity_setpoint: Optional[float] = None,
        effective_co2_setpoint: Optional[float] = None,
        effective_vpd_setpoint: Optional[float] = None,
        nominal_heating_setpoint: Optional[float] = None,
        nominal_cooling_setpoint: Optional[float] = None,
        nominal_humidity_setpoint: Optional[float] = None,
        nominal_co2_setpoint: Optional[float] = None,
        nominal_vpd_setpoint: Optional[float] = None,
        ramp_progress_heating: Optional[float] = None,
        ramp_progress_cooling: Optional[float] = None,
        ramp_progress_humidity: Optional[float] = None,
        ramp_progress_co2: Optional[float] = None,
        ramp_progress_vpd: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> bool:
        """Log effective setpoints to effective_setpoints table.
        
        This method logs both effective and nominal setpoints at the location/cluster
        level, along with ramp progress information. Called at every control step to
        provide a complete historical record of what the system was targeting.
        
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
            timestamp: Optional timestamp (defaults to NOW())
        
        Returns:
            True if successful, False otherwise
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                ts = timestamp or datetime.now()
                db_mode = mode if mode else None
                
                await conn.execute("""
                    INSERT INTO effective_setpoints 
                    (timestamp, location, cluster, mode, 
                     effective_heating_setpoint, effective_cooling_setpoint,
                     effective_humidity_setpoint, effective_co2_setpoint, effective_vpd_setpoint,
                     nominal_heating_setpoint, nominal_cooling_setpoint,
                     nominal_humidity_setpoint, nominal_co2_setpoint, nominal_vpd_setpoint,
                     ramp_progress_heating, ramp_progress_cooling,
                     ramp_progress_humidity, ramp_progress_co2, ramp_progress_vpd)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                """, ts, location, cluster, db_mode,
                    effective_heating_setpoint, effective_cooling_setpoint,
                    effective_humidity_setpoint, effective_co2_setpoint, effective_vpd_setpoint,
                    nominal_heating_setpoint, nominal_cooling_setpoint,
                    nominal_humidity_setpoint, nominal_co2_setpoint, nominal_vpd_setpoint,
                    ramp_progress_heating, ramp_progress_cooling,
                    ramp_progress_humidity, ramp_progress_co2, ramp_progress_vpd)
                
                # Write effective setpoints to Redis for fast access
                if self._automation_redis and self._automation_redis.redis_enabled:
                    self._automation_redis.write_effective_setpoints(
                        location, cluster,
                        effective_heating_setpoint,
                        effective_cooling_setpoint,
                        effective_humidity_setpoint,
                        effective_co2_setpoint,
                        effective_vpd_setpoint
                    )
                
                return True
        except Exception as e:
            logger.error(f"Error logging effective setpoints: {e}")
            return False
    
    async def get_all_setpoints_for_location_cluster(self, location: str, cluster: str) -> List[Dict[str, Any]]:
        """Get all setpoints for a location/cluster (all modes).
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            List of setpoint dicts, each with mode information
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT DISTINCT ON (mode) heating_setpoint, cooling_setpoint, humidity, co2, vpd, mode, updated_at
                    FROM setpoints
                    WHERE location = $1 AND cluster = $2
                    ORDER BY mode NULLS FIRST, updated_at DESC
                """, location, cluster)
                
                return [
                    {
                        'heating_setpoint': row['heating_setpoint'],
                        'cooling_setpoint': row['cooling_setpoint'],
                        'humidity': row['humidity'],
                        'co2': row['co2'],
                        'vpd': row['vpd'],
                        'mode': row['mode'],
                        'updated_at': row['updated_at']
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Error getting all setpoints: {e}")
            return []
    
    async def get_all_device_states(self) -> List[Dict[str, Any]]:
        """Get all device states."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT location, cluster, device_name, channel, state, mode, updated_at
                    FROM device_states
                    ORDER BY location, cluster, device_name
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all device states: {e}")
            return []
    
    async def get_device_mapping(
        self,
        location: str,
        cluster: str,
        device_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get device mapping from database.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
        
        Returns:
            Dict with channel, active_high, safe_state, mcp_board_id, updated_at, or None if not found
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT channel, active_high, safe_state, mcp_board_id, updated_at
                    FROM device_mappings
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                """, location, cluster, device_name)
                
                if row:
                    return {
                        'channel': row['channel'],
                        'active_high': row['active_high'],
                        'safe_state': row['safe_state'],
                        'mcp_board_id': row['mcp_board_id'],
                        'updated_at': row['updated_at']
                    }
        except Exception as e:
            logger.error(f"Error getting device mapping: {e}")
        return None
    
    async def set_device_mapping(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        active_high: bool = True,
        safe_state: int = 0,
        mcp_board_id: Optional[int] = None
    ) -> bool:
        """Set device mapping in database.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            channel: MCP23017 channel number (0-15)
            active_high: True if active high logic, False if active low
            safe_state: Safe state (0 or 1)
            mcp_board_id: MCP23017 board ID (optional)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO device_mappings (location, cluster, device_name, channel, active_high, safe_state, mcp_board_id, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                    ON CONFLICT (location, cluster, device_name)
                    DO UPDATE SET 
                        channel = EXCLUDED.channel,
                        active_high = EXCLUDED.active_high,
                        safe_state = EXCLUDED.safe_state,
                        mcp_board_id = EXCLUDED.mcp_board_id,
                        updated_at = NOW()
                """, location, cluster, device_name, channel, active_high, safe_state, mcp_board_id)
                logger.info(f"Device mapping updated: {location}/{cluster}/{device_name} -> channel {channel}")
                return True
        except Exception as e:
            logger.error(f"Error setting device mapping: {e}")
            return False
    
    async def get_all_device_mappings(self) -> List[Dict[str, Any]]:
        """Get all device mappings.
        
        Returns:
            List of device mapping dicts
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT location, cluster, device_name, channel, active_high, safe_state, mcp_board_id, updated_at
                    FROM device_mappings
                    ORDER BY location, cluster, device_name
                """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all device mappings: {e}")
            return []
    
    async def get_pid_parameters(self, device_type: str) -> Optional[Dict[str, Any]]:
        """Get PID parameters from database.
        
        Args:
            device_type: Device type (e.g., 'heater', 'co2')
        
        Returns:
            Dict with 'kp', 'ki', 'kd', 'updated_at', 'updated_by', 'source', or None if not found
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT kp, ki, kd, updated_at, updated_by, source
                    FROM pid_parameters
                    WHERE device_type = $1
                """, device_type)
                
                if row:
                    return {
                        'kp': row['kp'],
                        'ki': row['ki'],
                        'kd': row['kd'],
                        'updated_at': row['updated_at'],
                        'updated_by': row['updated_by'],
                        'source': row['source']
                    }
        except Exception as e:
            logger.error(f"Error getting PID parameters: {e}")
        return None
    
    async def set_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = 'api',
        updated_by: Optional[str] = None
    ) -> bool:
        """Set PID parameters in database with logging.
        
        Args:
            device_type: Device type (e.g., 'heater', 'co2')
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            source: Source of update ('api', 'config')
            updated_by: Optional identifier of who made the update
        
        Returns:
            True if successful, False otherwise
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Get existing parameters for history
                existing = await self.get_pid_parameters(device_type)
                
                # Update or insert PID parameters
                await conn.execute("""
                    INSERT INTO pid_parameters (device_type, kp, ki, kd, updated_at, updated_by, source)
                    VALUES ($1, $2, $3, $4, NOW(), $5, $6)
                    ON CONFLICT (device_type)
                    DO UPDATE SET 
                        kp = EXCLUDED.kp,
                        ki = EXCLUDED.ki,
                        kd = EXCLUDED.kd,
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by,
                        source = EXCLUDED.source
                """, device_type, kp, ki, kd, updated_by, source)
                
                # Log to history if parameters changed
                if existing is None or existing['kp'] != kp or existing['ki'] != ki or existing['kd'] != kd:
                    await conn.execute("""
                        INSERT INTO pid_parameter_history (timestamp, device_type, kp, ki, kd, updated_by, source)
                        VALUES (NOW(), $1, $2, $3, $4, $5, $6)
                    """, device_type, kp, ki, kd, updated_by, source)
                    logger.info(f"PID parameters updated for {device_type}: Kp={kp}, Ki={ki}, Kd={kd} (source: {source})")
                
                return True
        except Exception as e:
            logger.error(f"Error setting PID parameters: {e}")
            return False
    
    async def get_pid_parameter_history(
        self,
        device_type: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get PID parameter change history.
        
        Args:
            device_type: Device type
            limit: Maximum number of history entries to return
        
        Returns:
            List of history entries with timestamp, kp, ki, kd, updated_by, source
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT timestamp, kp, ki, kd, updated_by, source
                    FROM pid_parameter_history
                    WHERE device_type = $1
                    ORDER BY timestamp DESC
                    LIMIT $2
                """, device_type, limit)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting PID parameter history: {e}")
            return []
    
    async def get_all_pid_parameters(self) -> Dict[str, Dict[str, Any]]:
        """Get all PID parameters for all device types.
        
        Returns:
            Dict mapping device_type to parameter dict
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT device_type, kp, ki, kd, updated_at, updated_by, source
                    FROM pid_parameters
                    ORDER BY device_type
                """)
                return {row['device_type']: {
                    'kp': row['kp'],
                    'ki': row['ki'],
                    'kd': row['kd'],
                    'updated_at': row['updated_at'],
                    'updated_by': row['updated_by'],
                    'source': row['source']
                } for row in rows}
        except Exception as e:
            logger.error(f"Error getting all PID parameters: {e}")
            return {}
    
    async def get_schedules(
        self,
        location: Optional[str] = None,
        cluster: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get schedules from database.
        
        Args:
            location: Filter by location (optional)
            cluster: Filter by cluster (optional)
        
        Returns:
            List of schedule dictionaries
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                if location and cluster:
                    rows = await conn.fetch("""
                        SELECT id, name, location, cluster, device_name, day_of_week,
                               start_time, end_time, enabled, mode, target_intensity,
                               ramp_up_duration, ramp_down_duration, pre_day_duration,
                               pre_night_duration, created_at
                        FROM schedules
                        WHERE location = $1 AND cluster = $2
                        ORDER BY start_time
                    """, location, cluster)
                elif location:
                    rows = await conn.fetch("""
                        SELECT id, name, location, cluster, device_name, day_of_week,
                               start_time, end_time, enabled, mode, target_intensity,
                               ramp_up_duration, ramp_down_duration, pre_day_duration,
                               pre_night_duration, created_at
                        FROM schedules
                        WHERE location = $1
                        ORDER BY start_time
                    """, location)
                else:
                    rows = await conn.fetch("""
                        SELECT id, name, location, cluster, device_name, day_of_week,
                               start_time, end_time, enabled, mode, target_intensity,
                               ramp_up_duration, ramp_down_duration, pre_day_duration,
                               pre_night_duration, created_at
                        FROM schedules
                        ORDER BY location, cluster, start_time
                    """)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting schedules: {e}")
            return []
    
    async def get_climate_schedule(
        self,
        location: str,
        cluster: str
    ) -> Optional[Dict[str, Any]]:
        """Get climate schedule data (pre-day/pre-night durations) for a location/cluster.
        
        Climate schedules are stored in schedules table with pre_day_duration/pre_night_duration.
        This method finds the first schedule with these fields set (or returns defaults).
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict with pre_day_duration, pre_night_duration, day_start_time, day_end_time
            or None if not found
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Get climate schedule (any schedule with pre_day_duration or pre_night_duration set)
                row = await conn.fetchrow("""
                    SELECT pre_day_duration, pre_night_duration
                    FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND (pre_day_duration IS NOT NULL OR pre_night_duration IS NOT NULL)
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, location, cluster)
                
                if row:
                    return {
                        'pre_day_duration': row['pre_day_duration'] or 0,
                        'pre_night_duration': row['pre_night_duration'] or 0
                    }
                
                # If no climate schedule found, return defaults
                return {
                    'pre_day_duration': 0,
                    'pre_night_duration': 0
                }
        except Exception as e:
            logger.error(f"Error getting climate schedule: {e}")
            return {
                'pre_day_duration': 0,
                'pre_night_duration': 0
            }
    
    async def get_light_schedule(
        self,
        location: str,
        cluster: str
    ) -> Optional[Dict[str, Any]]:
        """Get light schedule (day start/end times) for a location/cluster.
        
        Finds the first enabled DAY mode schedule for any light device.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            Dict with day_start_time, day_end_time or None if not found
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                # Get first enabled DAY mode schedule (for lights)
                row = await conn.fetchrow("""
                    SELECT start_time, end_time
                    FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND enabled = TRUE
                      AND mode = 'DAY'
                    ORDER BY start_time
                    LIMIT 1
                """, location, cluster)
                
                if row:
                    return {
                        'day_start_time': str(row['start_time']),
                        'day_end_time': str(row['end_time'])
                    }
        except Exception as e:
            logger.error(f"Error getting light schedule: {e}")
        return None
    
    async def create_schedule(
        self,
        name: str,
        location: str,
        cluster: str,
        device_name: str,
        start_time: str,
        end_time: str,
        day_of_week: Optional[int] = None,
        enabled: bool = True,
        mode: Optional[str] = None,
        target_intensity: Optional[float] = None,
        ramp_up_duration: Optional[int] = None,
        ramp_down_duration: Optional[int] = None,
        conn: Optional[asyncpg.Connection] = None
    ) -> Optional[int]:
        """Create a new schedule.
        
        Args:
            name: Schedule name
            location: Location name
            cluster: Cluster name
            device_name: Device name
            start_time: Start time (HH:MM format)
            end_time: End time (HH:MM format)
            day_of_week: Day of week (0-6, None for daily)
            enabled: Whether schedule is enabled
            mode: Mode (DAY, NIGHT, TRANSITION) for mode-based scheduling
            target_intensity: Target light intensity (0-100%) for ramp schedules
            ramp_up_duration: Ramp up duration in minutes (0 = instant)
            ramp_down_duration: Ramp down duration in minutes (0 = instant)
            conn: Optional database connection (for use within transactions)
        
        Returns:
            Schedule ID if successful, None otherwise
        """
        try:
            # Convert time strings to TIME objects
            from datetime import time as dt_time
            start_parts = start_time.split(':')
            end_parts = end_time.split(':')
            start_time_obj = dt_time(int(start_parts[0]), int(start_parts[1]))
            end_time_obj = dt_time(int(end_parts[0]), int(end_parts[1]))
            
            if conn is not None:
                # Use provided connection (within transaction)
                row = await conn.fetchrow("""
                    INSERT INTO schedules 
                    (name, location, cluster, device_name, day_of_week, start_time, end_time, enabled, mode,
                     target_intensity, ramp_up_duration, ramp_down_duration)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    RETURNING id
                """, name, location, cluster, device_name, day_of_week, start_time_obj, end_time_obj, enabled, mode,
                    target_intensity, ramp_up_duration, ramp_down_duration)
                return row['id'] if row else None
            else:
                # Create new connection
                pool = await self._get_pool()
                async with pool.acquire() as new_conn:
                    row = await new_conn.fetchrow("""
                        INSERT INTO schedules 
                        (name, location, cluster, device_name, day_of_week, start_time, end_time, enabled, mode,
                         target_intensity, ramp_up_duration, ramp_down_duration)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                        RETURNING id
                    """, name, location, cluster, device_name, day_of_week, start_time_obj, end_time_obj, enabled, mode,
                        target_intensity, ramp_up_duration, ramp_down_duration)
                    return row['id'] if row else None
        except Exception as e:
            logger.error(f"Error creating schedule: {e}")
            raise  # Re-raise to allow transaction rollback
    
    async def update_schedule(
        self,
        schedule_id: int,
        name: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        day_of_week: Optional[int] = None,
        enabled: Optional[bool] = None,
        mode: Optional[str] = None,
        target_intensity: Optional[float] = None,
        ramp_up_duration: Optional[int] = None,
        ramp_down_duration: Optional[int] = None
    ) -> bool:
        """Update a schedule.
        
        Args:
            schedule_id: Schedule ID
            name: New name (optional)
            start_time: New start time (optional)
            end_time: New end time (optional)
            day_of_week: New day of week (optional)
            enabled: New enabled state (optional)
            mode: New mode (optional)
            target_intensity: New target intensity (optional)
            ramp_up_duration: New ramp up duration in minutes (optional)
            ramp_down_duration: New ramp down duration in minutes (optional)
        
        Returns:
            True if successful, False otherwise
        """
        # Whitelist of allowed column names for security
        ALLOWED_COLUMNS = {
            'name', 'start_time', 'end_time', 'day_of_week', 'enabled',
            'mode', 'target_intensity', 'ramp_up_duration', 'ramp_down_duration'
        }
        
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                updates = []
                params = []
                param_idx = 1
                
                if name is not None:
                    if 'name' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'name' is not allowed")
                    updates.append(f"name = ${param_idx}")
                    params.append(name)
                    param_idx += 1
                if start_time is not None:
                    if 'start_time' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'start_time' is not allowed")
                    # Convert time string to TIME object
                    from datetime import time as dt_time
                    start_parts = start_time.split(':')
                    start_time_obj = dt_time(int(start_parts[0]), int(start_parts[1]))
                    updates.append(f"start_time = ${param_idx}")
                    params.append(start_time_obj)
                    param_idx += 1
                if end_time is not None:
                    if 'end_time' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'end_time' is not allowed")
                    # Convert time string to TIME object
                    from datetime import time as dt_time
                    end_parts = end_time.split(':')
                    end_time_obj = dt_time(int(end_parts[0]), int(end_parts[1]))
                    updates.append(f"end_time = ${param_idx}")
                    params.append(end_time_obj)
                    param_idx += 1
                if day_of_week is not None:
                    if 'day_of_week' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'day_of_week' is not allowed")
                    updates.append(f"day_of_week = ${param_idx}")
                    params.append(day_of_week)
                    param_idx += 1
                if enabled is not None:
                    if 'enabled' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'enabled' is not allowed")
                    updates.append(f"enabled = ${param_idx}")
                    params.append(enabled)
                    param_idx += 1
                if mode is not None:
                    if 'mode' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'mode' is not allowed")
                    updates.append(f"mode = ${param_idx}")
                    params.append(mode)
                    param_idx += 1
                if target_intensity is not None:
                    if 'target_intensity' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'target_intensity' is not allowed")
                    updates.append(f"target_intensity = ${param_idx}")
                    params.append(target_intensity)
                    param_idx += 1
                if ramp_up_duration is not None:
                    if 'ramp_up_duration' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'ramp_up_duration' is not allowed")
                    updates.append(f"ramp_up_duration = ${param_idx}")
                    params.append(ramp_up_duration)
                    param_idx += 1
                if ramp_down_duration is not None:
                    if 'ramp_down_duration' not in ALLOWED_COLUMNS:
                        raise ValueError("Column 'ramp_down_duration' is not allowed")
                    updates.append(f"ramp_down_duration = ${param_idx}")
                    params.append(ramp_down_duration)
                    param_idx += 1
                
                if not updates:
                    return False
                
                params.append(schedule_id)
                # Use parameterized query with whitelisted column names
                query = f"""
                    UPDATE schedules
                    SET {', '.join(updates)}
                    WHERE id = ${param_idx}
                """
                await conn.execute(query, *params)
                return True
        except Exception as e:
            logger.error(f"Error updating schedule: {e}")
            return False
    
    async def delete_schedule(self, schedule_id: int) -> bool:
        """Delete a schedule.
        
        Args:
            schedule_id: Schedule ID
        
        Returns:
            True if successful, False otherwise
        """
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM schedules WHERE id = $1", schedule_id)
                return True
        except Exception as e:
            logger.error(f"Error deleting schedule: {e}")
            return False
    
    async def delete_schedules_bulk(self, schedule_ids: List[int], conn: asyncpg.Connection) -> int:
        """Delete multiple schedules within a transaction.
        
        Args:
            schedule_ids: List of schedule IDs to delete
            conn: Database connection (must be within a transaction)
        
        Returns:
            Number of schedules deleted
        """
        if not schedule_ids:
            return 0
        try:
            result = await conn.execute(
                "DELETE FROM schedules WHERE id = ANY($1::bigint[])",
                schedule_ids
            )
            # Extract number of rows deleted from result string
            deleted_count = int(result.split()[-1]) if result else 0
            logger.info(f"Deleted {deleted_count} schedules in bulk")
            return deleted_count
        except Exception as e:
            logger.error(f"Error deleting schedules in bulk: {e}")
            raise
    
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

