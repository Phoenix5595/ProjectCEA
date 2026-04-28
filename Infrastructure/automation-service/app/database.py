"""Database manager for TimescaleDB operations."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import asyncpg
import redis

from shared.db import create_pool, db_config_from_env
from shared.infra_logging import get_logger

from .migrations import create_room_modes_tables, run_alembic_migrations
from .redis_client import AutomationRedisClient
from .repositories.climate_periods import ClimatePeriodRepository
from .repositories.config import ConfigRepository
from .repositories.control_actions import ControlActionRepository
from .repositories.devices import DeviceRepository
from .repositories.pid import PIDRepository
from .repositories.room_modes import RoomModeRepository
from .repositories.schedules import ScheduleRepository
from .repositories.sensors import SensorRepository
from .repositories.setpoints import SetpointRepository

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
        self.db_config = db_config if db_config is not None else db_config_from_env()
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._pool: asyncpg.Pool | None = None
        self._redis_client: redis.Redis | None = None
        self._redis_enabled = False
        self._automation_redis: AutomationRedisClient | None = None
        self._db_connected = False

        # Repository instances (initialized in initialize())
        self._sensor_repo: SensorRepository | None = None
        self._device_repo: DeviceRepository | None = None
        self._setpoint_repo: SetpointRepository | None = None
        self._schedule_repo: ScheduleRepository | None = None
        self._pid_repo: PIDRepository | None = None
        self._room_mode_repo: RoomModeRepository | None = None
        self._control_action_repo: ControlActionRepository | None = None
        self._config_repo: ConfigRepository | None = None
        self._climate_periods_repo: ClimatePeriodRepository | None = None

    async def initialize(self) -> bool:
        """Initialize database connection and run migrations.

        Returns:
            True if successful, False otherwise
        """
        try:
            await self._connect_db()

            # Run migrations
            run_alembic_migrations()
            if self._pool:
                await create_room_modes_tables(self._pool)

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
            self._config_repo = ConfigRepository(self._pool)
            self._climate_periods_repo = ClimatePeriodRepository(self._pool)

            return True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            return False

    async def _connect_db(self) -> None:
        """Connect to TimescaleDB; retry/backoff handled by shared.db.create_pool."""
        self._pool = await create_pool(self.db_config, application_name="automation_service")
        self._db_connected = True

    async def _connect_redis(self) -> None:
        """Connect to Redis."""
        try:
            self._redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            await asyncio.to_thread(self._redis_client.ping)
            self._redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url}")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will use TimescaleDB fallback.")
            self._redis_enabled = False

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool with retry logic."""
        if self._pool is None or not self._db_connected:
            await self._connect_db()
        return self._pool  # pyright: ignore[reportReturnType]

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

    async def load_schedule_state_to_redis(self) -> None:
        """Load all schedule state from database to Redis following canonical schema."""
        # Lazy import to avoid circular dependency (services/__init__.py -> mode_transition_service -> database)
        from .services.schedule_state import (
            load_schedule_state_to_redis as _load_schedule_state,
        )

        if (
            not self._automation_redis
            or not self._pool
            or not self._schedule_repo
            or not self._climate_periods_repo
        ):
            logger.warning("Components not initialized, skipping schedule state load")
            return

        # load_schedule_state_to_redis expects a ClimatePeriodRepository as the
        # 4th argument; this previously passed self._setpoint_repo, which caused
        # `'SetpointRepository' object has no attribute 'get_periods'` at every
        # startup and left Redis schedule state incomplete (periods empty) for
        # every cluster.
        await _load_schedule_state(
            self._pool, self._automation_redis, self._schedule_repo, self._climate_periods_repo
        )

    # Repository properties for access
    @property
    def sensor_repo(self) -> SensorRepository:
        if not self._sensor_repo:
            raise RuntimeError("SensorRepository not initialized")
        return self._sensor_repo

    @property
    def device_repo(self) -> DeviceRepository:
        if not self._device_repo:
            raise RuntimeError("DeviceRepository not initialized")
        return self._device_repo

    @property
    def setpoint_repo(self) -> SetpointRepository:
        if not self._setpoint_repo:
            raise RuntimeError("SetpointRepository not initialized")
        return self._setpoint_repo

    @property
    def climate_periods_repo(self) -> ClimatePeriodRepository:
        if not self._climate_periods_repo:
            raise RuntimeError("ClimatePeriodRepository not initialized")
        return self._climate_periods_repo

    @property
    def schedule_repo(self) -> ScheduleRepository:
        if not self._schedule_repo:
            raise RuntimeError("ScheduleRepository not initialized")
        return self._schedule_repo

    @property
    def pid_repo(self) -> PIDRepository:
        if not self._pid_repo:
            raise RuntimeError("PIDRepository not initialized")
        return self._pid_repo

    @property
    def room_mode_repo(self) -> RoomModeRepository:
        if not self._room_mode_repo:
            raise RuntimeError("RoomModeRepository not initialized")
        return self._room_mode_repo

    @property
    def control_action_repo(self) -> ControlActionRepository:
        if not self._control_action_repo:
            raise RuntimeError("ControlActionRepository not initialized")
        return self._control_action_repo

    @property
    def config_repo(self) -> ConfigRepository:
        if not self._config_repo:
            raise RuntimeError("ConfigRepository not initialized")
        return self._config_repo

    @property
    def automation_redis(self) -> AutomationRedisClient | None:
        return self._automation_redis

    @property
    def pool(self) -> asyncpg.Pool | None:
        return self._pool
