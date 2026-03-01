"""Service container for dependency injection and initialization."""

from __future__ import annotations

from typing import Any

from app.alarm_manager import AlarmManager
from app.automation.interlock_manager import InterlockManager
from app.automation.rules_engine import RulesEngine
from app.background_tasks import BackgroundTasks
from app.config import ConfigLoader
from app.control.control_engine import ControlEngine
from app.control.relay_manager import RelayManager
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager
from app.hardware.mcp23017 import MCP23017Driver
from app.redis_client import AutomationRedisClient
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ServiceContainer:
    """Dependency injection container for automation service.

    Manages initialization and lifecycle of all service components.
    Components are initialized in dependency order during startup.
    """

    def __init__(self):
        """Initialize container with None values."""
        # Configuration
        self.config: ConfigLoader | None = None

        # Database and Redis
        self.database: DatabaseManager | None = None
        self.automation_redis: AutomationRedisClient | None = None

        # Hardware
        self.mcp23017: MCP23017Driver | None = None
        self.dfr0971_manager: DFR0971Manager | None = None

        # Control components
        self.interlock_manager: InterlockManager | None = None
        self.relay_manager: RelayManager | None = None
        self.scheduler: Scheduler | None = None
        self.rules_engine: RulesEngine | None = None
        self.alarm_manager: AlarmManager | None = None
        self.control_engine: ControlEngine | None = None

        # Background tasks
        self.background_tasks: BackgroundTasks | None = None

        # State tracking
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize all service components in dependency order.

        Order:
        1. Config
        2. Database & Redis
        3. Hardware (MCP23017, DFR0971)
        4. Interlock Manager
        5. Relay Manager
        6. Scheduler (with schedules from DB)
        7. Rules Engine
        8. Alarm Manager
        9. Control Engine
        10. Background Tasks
        """
        if self._initialized:
            logger.warning("ServiceContainer already initialized")
            return

        logger.info("Initializing service container...")

        try:
            # 1. Load configuration
            self.config = ConfigLoader()
            logger.info("Configuration loaded")

            # 2. Initialize database
            self.database = DatabaseManager()
            await self.database.initialize()
            logger.info("Database initialized")

            # Get automation redis from database
            self.automation_redis = self.database._automation_redis

            # Load schedule state from DB to Redis (after Redis connection is established)
            try:
                await self.database.load_schedule_state_to_redis()
            except Exception as e:
                logger.warning(f"Failed to load schedule state to Redis: {e}")

            # 3. Initialize hardware
            await self._init_hardware()

            # 4. Initialize interlock manager
            devices = self.config.get_devices()
            interlocks = self.config.get("interlocks", [])
            self.interlock_manager = InterlockManager(
                device_config=devices, interlock_rules=interlocks
            )
            logger.info("Interlock manager initialized")

            # 5. Initialize relay manager
            assert self.mcp23017 is not None, "MCP23017 driver must be initialized"
            self.relay_manager = RelayManager(
                mcp23017=self.mcp23017,
                device_config=devices,
                interlock_manager=self.interlock_manager,
            )
            logger.info("Relay manager initialized")

            # 6. Initialize scheduler with schedules from database
            db_schedules = await self.database.schedule_repo.get_schedules()
            self.scheduler = Scheduler(db_schedules)
            logger.info(f"Scheduler initialized with {len(db_schedules)} schedules")

            # 7. Initialize rules engine
            # get_rules is not yet implemented in DatabaseManager
            rules: list[dict[str, Any]] = []
            self.rules_engine = RulesEngine(rules, self.scheduler)
            logger.info("Rules engine initialized")

            # 8. Initialize alarm manager
            assert self.automation_redis is not None, "AutomationRedisClient must be initialized"
            self.alarm_manager = AlarmManager(self.automation_redis, self.database)
            logger.info("Alarm manager initialized")

            # 9. Initialize control engine
            self.control_engine = ControlEngine(
                relay_manager=self.relay_manager,
                database=self.database,
                config=self.config,
                scheduler=self.scheduler,
                rules_engine=self.rules_engine,
                alarm_manager=self.alarm_manager,
                dfr0971_manager=self.dfr0971_manager,
            )
            logger.info("Control engine initialized")

            # Restore ramp state from database (for service restarts)
            try:
                await self.control_engine.restore_ramp_state_from_database()
            except Exception as e:
                logger.warning(f"Failed to restore ramp state: {e}")

            # Initialize lights (safety levels and restore intensities)
            from app.initialization.lighting import restore_light_intensities, set_safety_levels

            await set_safety_levels(self.config, self.dfr0971_manager)
            await restore_light_intensities(self.database, self.config, self.dfr0971_manager)

            # 10. Initialize background tasks (control loop max 5s, non-negotiable)
            update_interval = self.config.get_update_interval()
            self.background_tasks = BackgroundTasks(
                control_engine=self.control_engine,
                database=self.database,
                update_interval=update_interval,
                alarm_manager=self.alarm_manager,
            )
            logger.info("Background tasks initialized")

            # Start background tasks
            await self.background_tasks.start()
            logger.info("Background tasks started")

            self._initialized = True
            logger.info("Service container initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize service container: {e}", exc_info=True)
            await self.shutdown()
            raise

    async def _init_hardware(self) -> None:
        """Initialize hardware components.

        MCP23017 = relays only (on/off), typically bus 0.
        DFR0971 = dimming only (0-10V), typically bus 1.
        """
        assert self.config is not None, "Config must be loaded before hardware initialization"
        hardware_config = self.config.get("hardware", {})
        simulation = hardware_config.get("simulation", False)
        i2c_bus_legacy = hardware_config.get("i2c_bus", 1)

        # Separate buses: MCP for relays, DFR0971 for dimming (fallback to legacy i2c_bus)
        mcp_i2c_bus = hardware_config.get("mcp_i2c_bus", i2c_bus_legacy)
        dfr0971_i2c_bus = hardware_config.get("dfr0971_i2c_bus", i2c_bus_legacy)
        i2c_address = hardware_config.get("i2c_address", 32)
        dfr0971_boards = hardware_config.get("dfr0971_boards", [])

        if dfr0971_boards and mcp_i2c_bus == dfr0971_i2c_bus:
            logger.warning(
                "MCP and DFR0971 share the same I2C bus (%s); expected: MCP on bus 0 (relays), "
                "DFR0971 on bus 1 (dimming)",
                mcp_i2c_bus,
            )

        # Initialize MCP23017 relay driver (relays only)
        require_mcp = hardware_config.get("require_mcp", False)
        try:
            self.mcp23017 = MCP23017Driver(
                i2c_bus=mcp_i2c_bus, i2c_address=i2c_address, simulation=simulation
            )
            logger.info(
                f"MCP23017 initialized on bus {mcp_i2c_bus} (relays only, addr=0x{i2c_address:02x}, simulation={simulation})"
            )
            if not simulation and not self.mcp23017.probe():
                if require_mcp:
                    raise RuntimeError(
                        "MCP23017 probe failed (I2C not responding); require_mcp is true"
                    )
                logger.warning("MCP23017 probe failed, falling back to simulation mode")
                self.mcp23017 = MCP23017Driver(simulation=True)
        except Exception as e:
            if require_mcp and "probe failed" in str(e):
                raise
            logger.error(f"Failed to initialize MCP23017: {e}")
            self.mcp23017 = MCP23017Driver(simulation=True)
            logger.warning("Using MCP23017 in simulation mode")

        # Initialize DFR0971 light dimming manager (dimming only)
        if dfr0971_boards:
            try:
                self.dfr0971_manager = DFR0971Manager(
                    i2c_bus=dfr0971_i2c_bus, simulation=simulation
                )
                # Add each board to the manager
                for board in dfr0971_boards:
                    board_id = board.get("board_id", 0)
                    i2c_addr = board.get("i2c_address", 0x58)
                    board_name = board.get("name", f"Board {board_id}")
                    self.dfr0971_manager.add_board(board_id, i2c_addr, board_name)
                logger.info(
                    f"DFR0971 manager initialized on bus {dfr0971_i2c_bus} (dimming only) "
                    f"with {len(dfr0971_boards)} boards"
                )
            except Exception as e:
                logger.error(f"Failed to initialize DFR0971 manager: {e}")
                self.dfr0971_manager = None
        else:
            logger.info("No DFR0971 boards configured")
            self.dfr0971_manager = None

    async def shutdown(self) -> None:
        """Shutdown all service components gracefully."""
        logger.info("Shutting down service container...")

        # Stop background tasks first
        if self.background_tasks:
            try:
                await self.background_tasks.stop()
                logger.info("Background tasks stopped")
            except Exception as e:
                logger.error(f"Error stopping background tasks: {e}")

        # Close database connection
        if self.database:
            try:
                await self.database.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")

        self._initialized = False
        logger.info("Service container shutdown complete")

    def get_database(self) -> DatabaseManager:
        """Get database manager instance."""
        if not self.database:
            raise RuntimeError("Database not initialized")
        return self.database

    def get_config(self) -> ConfigLoader:
        """Get config loader instance."""
        if not self.config:
            raise RuntimeError("Config not initialized")
        return self.config

    def get_scheduler(self) -> Scheduler:
        """Get scheduler instance."""
        if not self.scheduler:
            raise RuntimeError("Scheduler not initialized")
        return self.scheduler

    def get_relay_manager(self) -> RelayManager:
        """Get relay manager instance."""
        if not self.relay_manager:
            raise RuntimeError("Relay manager not initialized")
        return self.relay_manager

    def get_dfr0971_manager(self) -> DFR0971Manager | None:
        """Get DFR0971 manager instance."""
        return self.dfr0971_manager

    def get_interlock_manager(self) -> InterlockManager:
        """Get interlock manager instance."""
        if not self.interlock_manager:
            raise RuntimeError("Interlock manager not initialized")
        return self.interlock_manager

    def get_alarm_manager(self) -> AlarmManager | None:
        """Get alarm manager instance."""
        return self.alarm_manager

    def get_control_engine(self) -> ControlEngine:
        """Get control engine instance."""
        if not self.control_engine:
            raise RuntimeError("Control engine not initialized")
        return self.control_engine

    def get_pid_controller_manager(self):
        """Get PID controller manager from control engine (for status API load_percent)."""
        engine = self.get_control_engine()
        return getattr(engine, "pid_controller_manager", None)

    def get_automation_redis(self) -> AutomationRedisClient | None:
        """Get automation Redis client."""
        return self.automation_redis
