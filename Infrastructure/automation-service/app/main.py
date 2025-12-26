"""Main FastAPI application for automation service."""
import logging
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.hardware.mcp23017 import MCP23017Driver
from app.hardware.dfr0971 import DFR0971Manager
from app.control.relay_manager import RelayManager
from app.control.scheduler import Scheduler
from app.automation.rules_engine import RulesEngine
from app.automation.interlock_manager import InterlockManager
from app.control.control_engine import ControlEngine
from app.background_tasks import BackgroundTasks
from app.alarm_manager import AlarmManager

from app.routes import status, devices, setpoints, schedules, rules, pid, mode, failsafe, alarms, lights, websocket
from fastapi import Depends

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global instances (will be initialized in lifespan)
config: ConfigLoader = None
database: DatabaseManager = None
mcp23017: MCP23017Driver = None
dfr0971_manager: DFR0971Manager = None
relay_manager: RelayManager = None
scheduler: Scheduler = None
rules_engine: RulesEngine = None
interlock_manager: InterlockManager = None
control_engine: ControlEngine = None
background_tasks: BackgroundTasks = None
alarm_manager: AlarmManager = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global config, database, mcp23017, dfr0971_manager, relay_manager
    global scheduler, rules_engine, interlock_manager
    global control_engine, background_tasks, alarm_manager
    
    # Startup
    logger.info("Starting automation service...")
    
    try:
        # 1. Load configuration
        logger.info("Loading configuration...")
        config = ConfigLoader()
        
        # 2. Initialize database
        logger.info("Initializing database...")
        database = DatabaseManager()
        await database.initialize()
        
        # 3. Initialize hardware
        logger.info("Initializing hardware...")
        hardware_config = config.get_hardware_config()
        simulation = hardware_config.get('simulation', True)  # Default to simulation for safety
        
        mcp23017 = MCP23017Driver(
            i2c_bus=hardware_config.get('i2c_bus', 1),
            i2c_address=hardware_config.get('i2c_address', 0x20),
            simulation=simulation
        )
        
        # 3.5. Initialize DFR0971 manager for light dimming
        logger.info("Initializing DFR0971 manager...")
        dfr0971_manager = DFR0971Manager(
            i2c_bus=hardware_config.get('i2c_bus', 1),
            simulation=simulation
        )
        
        # Add configured DFR0971 boards
        dfr0971_boards_config = hardware_config.get('dfr0971_boards', [])
        if dfr0971_boards_config:
            for board_config in dfr0971_boards_config:
                board_id = board_config.get('board_id')
                i2c_address = board_config.get('i2c_address', 0x58)
                name = board_config.get('name')
                
                if board_id is not None:
                    success = dfr0971_manager.add_board(board_id, i2c_address, name)
                    if success:
                        logger.info(f"DFR0971 board {board_id} initialized at address 0x{i2c_address:02X}")
                    else:
                        logger.warning(f"Failed to initialize DFR0971 board {board_id}")
        else:
            logger.info("No DFR0971 boards configured")
        
        # 4. Create device load callback for interlock manager
        # This callback will be used to get device load percentages (intensity/duty cycle)
        def get_device_load(location: str, cluster: str, device_name: str) -> Optional[float]:
            """Get device load percentage (0-100) for interlock checking.
            
            Returns:
                Load percentage (0-100) or None if not available
            """
            devices = config.get_devices()
            device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
            
            if not device_info:
                return None
            
            # Check if it's a light with DFR0971 dimming
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                if dfr0971_manager:
                    board_id = device_info.get('dimming_board_id')
                    channel = device_info.get('dimming_channel')
                    if board_id is not None and channel is not None:
                        intensity = dfr0971_manager.get_intensity(board_id, channel)
                        if intensity is not None:
                            return intensity
                        # If intensity is None but relay is ON, assume 100%
                        # (device might be on but intensity not set yet)
                        relay_state = relay_manager.get_device_state(location, cluster, device_name) if relay_manager else None
                        if relay_state == 1:
                            return 100.0
                        return 0.0
            
            # For PWM devices (heaters, CO2), we'd need access to PID controllers
            # This will be enhanced later when control_engine is available
            # For now, return None (will fall back to ON/OFF check)
            
            return None
        
        # 5. Initialize interlock manager with load callback
        logger.info("Initializing interlock manager...")
        interlock_manager = InterlockManager(
            config.get_devices(),
            config.get_interlocks(),
            device_load_callback=get_device_load
        )
        
        # 6. Initialize relay manager
        logger.info("Initializing relay manager...")
        relay_manager = RelayManager(mcp23017, config.get_devices(), interlock_manager)
        
        # Update device load callback to include control_engine after it's created
        # (This will be done after control_engine initialization)
        
        # 7. Restore device states from database
        logger.info("Restoring device states...")
        db_states = await database.get_all_device_states()
        if db_states:
            # Convert to format expected by relay_manager
            states_dict = {}
            for state in db_states:
                key = (state['location'], state['cluster'], state['device_name'])
                states_dict[key] = {
                    'state': state['state'],
                    'mode': state['mode'],
                    'channel': state['channel']
                }
            relay_manager.restore_states(states_dict)
        else:
            # Use config defaults (all devices OFF)
            logger.info("No database states found, using config defaults")
        
        # 7. Initialize scheduler
        logger.info("Initializing scheduler...")
        schedules_list = config.get_schedules()
        scheduler = Scheduler(schedules_list)
        
        # 8. Initialize rules engine
        logger.info("Initializing rules engine...")
        rules_list = config.get_rules()
        rules_engine = RulesEngine(rules_list, scheduler)
        
        # 9. Initialize alarm manager
        logger.info("Initializing alarm manager...")
        if database._automation_redis:
            alarm_manager = AlarmManager(database._automation_redis, database)
        else:
            logger.warning("Redis not available, alarm manager will have limited functionality")
            alarm_manager = AlarmManager(None, database)
        
        # 10. Initialize control engine
        logger.info("Initializing control engine...")
        control_engine = ControlEngine(
            relay_manager, database, config, scheduler, rules_engine, alarm_manager, dfr0971_manager
        )
        
        # Update interlock manager's device load callback to include PID controllers
        # Create enhanced callback that can access both DFR0971 and PID controllers
        def get_device_load_enhanced(location: str, cluster: str, device_name: str) -> Optional[float]:
            """Enhanced device load callback with PID controller support."""
            devices = config.get_devices()
            device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
            
            if not device_info:
                return None
            
            # Check if it's a light with DFR0971 dimming
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                if dfr0971_manager:
                    board_id = device_info.get('dimming_board_id')
                    channel = device_info.get('dimming_channel')
                    if board_id is not None and channel is not None:
                        intensity = dfr0971_manager.get_intensity(board_id, channel)
                        if intensity is not None:
                            return intensity
                        # If intensity is None but relay is ON, assume 100%
                        relay_state = relay_manager.get_device_state(location, cluster, device_name)
                        if relay_state == 1:
                            return 100.0
                        return 0.0
            
            # Check if it's a PWM device (heater, CO2) with PID control
            if device_info.get('pid_enabled') and control_engine:
                # Get PID controller for this device
                key = (location, cluster, device_name)
                pid_controller = control_engine._pid_controllers.get(key)
                if pid_controller:
                    duty_cycle = pid_controller.get_duty_cycle()
                    # Also check if relay is actually ON
                    relay_state = relay_manager.get_device_state(location, cluster, device_name) or 0
                    if relay_state == 1:
                        return duty_cycle
                    return 0.0
            
            return None
        
        # Update the callback in interlock manager
        interlock_manager.device_load_callback = get_device_load_enhanced
        logger.info("Interlock manager enhanced with PID controller support")
        
        # 11. Initialize background tasks
        logger.info("Initializing background tasks...")
        update_interval = config.get_update_interval()
        background_tasks = BackgroundTasks(control_engine, database, update_interval, alarm_manager)
        await background_tasks.start()
        
        # 12. Sync config to database (setpoints, schedules, rules)
        logger.info("Syncing config to database...")
        await sync_config_to_database(config, database)
        
        # 13. Populate Redis from database on startup
        logger.info("Populating Redis from database...")
        await populate_redis_from_database(database, config)
        
        # Set safety levels for lights (per-device or per-board fallback)
        logger.info("Setting safety levels for lights...")
        devices = config.get_devices()
        safety_set_count = 0
        
        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                for device_name, device_info in cluster_devices.items():
                    # Check if this is a dimmable light
                    if not device_info.get('dimming_enabled', False):
                        continue
                    if device_info.get('dimming_type') != 'dfr0971':
                        continue
                    
                    board_id = device_info.get('dimming_board_id')
                    channel = device_info.get('dimming_channel')
                    device_safety_level = device_info.get('safety_level')
                    
                    if board_id is None or channel is None:
                        continue
                    
                    # Use device-level safety_level if specified, otherwise use board-level
                    if device_safety_level is not None:
                        # Device has its own safety level
                        if not simulation:
                            try:
                                dfr0971_manager.set_safety_level(board_id, channel, device_safety_level)
                                safety_set_count += 1
                                logger.info(
                                    f"Safety level {device_safety_level}% set for {location}/{cluster}/{device_name} "
                                    f"(board {board_id}, channel {channel})"
                                )
                            except Exception as e:
                                logger.warning(f"Could not set safety level for {location}/{cluster}/{device_name}: {e}")
        
        if safety_set_count > 0:
            logger.info(f"Set {safety_set_count} device-specific safety levels")
        
        # Restore light intensities from Redis/DB (since DFR0971 can't read EEPROM)
        # This restores the last user-selected or schedule-selected intensity levels.
        # Safety levels remain in EEPROM as the default on power-up.
        await restore_light_intensities_from_redis(database, config, dfr0971_manager)
        
        logger.info("Automation service started successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start automation service: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        logger.info("Shutting down automation service...")
        if dfr0971_manager:
            dfr0971_manager.close_all()
            logger.info("DFR0971 manager closed")
        
        if background_tasks:
            await background_tasks.stop()
        
        if database:
            await database.close()
        
        if mcp23017:
            mcp23017.close()
        
        logger.info("Automation service stopped")


async def sync_config_to_database(config: ConfigLoader, database: DatabaseManager):
    """Sync configuration to database tables."""
    # Sync setpoints
    default_setpoints = config.get_default_setpoints()
    for location, clusters in default_setpoints.items():
        for cluster, setpoints in clusters.items():
            await database.set_setpoint(
                location, cluster,
                setpoints.get('temperature'),
                setpoints.get('humidity'),
                setpoints.get('co2'),
                source='config'
            )
    
    # Sync PID parameters from config
    pid_config = config.get_pid_config()
    device_types = ['heater', 'co2']  # Add more as needed
    for device_type in device_types:
        params = config.get_pid_params_for_device(device_type)
        await database.set_pid_parameters(
            device_type,
            params['kp'],
            params['ki'],
            params['kd'],
            source='config'
        )
    
    # Sync schedules and rules would be done here
    # For now, they're loaded from config on startup


async def populate_redis_from_database(database: DatabaseManager, config: ConfigLoader):
    """Populate Redis from database on startup."""
    if not database._automation_redis or not database._automation_redis.redis_enabled:
        logger.warning("Redis not available, skipping population")
        return
    
    redis_client = database._automation_redis
    
    # Populate setpoints
    default_setpoints = config.get_default_setpoints()
    for location, clusters in default_setpoints.items():
        for cluster, setpoints in clusters.items():
            setpoint_data = await database.get_setpoint(location, cluster)
            if setpoint_data:
                redis_client.write_setpoint(
                    location, cluster,
                    setpoint_data.get('temperature'),
                    setpoint_data.get('humidity'),
                    setpoint_data.get('co2'),
                    source='api'
                )
                # Set default mode to 'auto'
                redis_client.write_mode(location, cluster, 'auto', source='system')
    
    # Populate PID parameters
    device_types = ['heater', 'co2']
    for device_type in device_types:
        params = await database.get_pid_parameters(device_type)
        if params:
            redis_client.write_pid_parameters(
                device_type,
                params['kp'],
                params['ki'],
                params['kd'],
                source=params.get('source', 'api'),
                updated_at=int(params.get('updated_at', 0).timestamp() * 1000) if params.get('updated_at') else None
            )
    
    logger.info("Redis populated from database")


async def restore_light_intensities_from_redis(
    database: DatabaseManager,
    config: ConfigLoader,
    dfr0971_manager
):
    """Restore light intensities on startup.
    
    Since DFR0971 cannot read EEPROM values, we restore from:
    1. Redis (fast, but may be lost on restart)
    2. Database (slower, but persistent - source of truth)
    
    The database is preferred since it persists across restarts and
    contains the logged intensity values.
    """
    if not dfr0971_manager:
        logger.warning("DFR0971 manager not available, skipping light intensity restoration")
        return
    
    logger.info("Restoring light intensities from database/Redis...")
    redis_client = database._automation_redis if database._automation_redis and database._automation_redis.redis_enabled else None
    devices = config.get_devices()
    restored_count = 0
    
    for location, clusters in devices.items():
        for cluster, cluster_devices in clusters.items():
            for device_name, device_info in cluster_devices.items():
                # Check if this is a dimmable light
                if not device_info.get('dimming_enabled', False):
                    continue
                
                if device_info.get('dimming_type') != 'dfr0971':
                    continue
                
                board_id = device_info.get('dimming_board_id')
                channel = device_info.get('dimming_channel')
                
                if board_id is None or channel is None:
                    continue
                
                intensity = None
                source = None
                
                # Try Redis first (fast, but may not persist)
                if redis_client:
                    light_data = redis_client.read_light_intensity(location, cluster, device_name)
                    if light_data:
                        intensity = light_data.get('intensity')
                        source = "Redis"
                
                # Fall back to database (slower, but persistent - source of truth)
                if intensity is None and database:
                    intensity = await database.get_latest_light_intensity(location, cluster, device_name)
                    if intensity is not None:
                        source = "Database"
                        # Also update Redis with the value we found in database
                        if redis_client:
                            voltage = (intensity / 100.0) * 10.0
                            redis_client.write_light_intensity(
                                location, cluster, device_name,
                                intensity, voltage, board_id, channel
                            )
                
                if intensity is not None:
                    # Restore intensity to hardware (but don't save to EEPROM - safety levels stay in EEPROM)
                    success = dfr0971_manager.set_intensity(
                        board_id, channel, intensity, store_to_eeprom=False
                    )
                    if success:
                        restored_count += 1
                        logger.info(
                            f"Restored {location}/{cluster}/{device_name} to {intensity:.1f}% "
                            f"from {source} (board {board_id}, channel {channel}, not saved to EEPROM)"
                        )
                    else:
                        logger.warning(
                            f"Failed to restore intensity for {location}/{cluster}/{device_name}"
                        )
    
    if restored_count > 0:
        logger.info(f"Restored {restored_count} light intensity values from database/Redis")
    else:
        logger.info("No light intensity values found to restore")


# Create FastAPI app
app = FastAPI(
    title="Automation Service",
    description="Device control and automation service for CEA system",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency injection functions
def get_config() -> ConfigLoader:
    """Get config loader."""
    return config


def get_database() -> DatabaseManager:
    """Get database manager."""
    return database


def get_relay_manager() -> RelayManager:
    """Get relay manager."""
    return relay_manager


def get_interlock_manager() -> InterlockManager:
    """Get interlock manager."""
    return interlock_manager


def get_scheduler() -> Scheduler:
    """Get scheduler."""
    return scheduler


def get_rules_engine() -> RulesEngine:
    """Get rules engine."""
    return rules_engine


def get_dfr0971_manager() -> DFR0971Manager:
    """Get DFR0971 manager."""
    return dfr0971_manager


# Override route dependencies
app.dependency_overrides[status.get_database] = get_database
app.dependency_overrides[status.get_relay_manager] = get_relay_manager
app.dependency_overrides[status.get_config] = get_config

app.dependency_overrides[devices.get_relay_manager] = get_relay_manager
app.dependency_overrides[devices.get_database] = get_database

app.dependency_overrides[setpoints.get_database] = get_database
app.dependency_overrides[setpoints.get_config] = get_config

app.dependency_overrides[schedules.get_database] = get_database
app.dependency_overrides[schedules.get_config] = get_config

app.dependency_overrides[rules.get_database] = get_database

app.dependency_overrides[pid.get_database] = get_database
app.dependency_overrides[pid.get_config] = get_config

app.dependency_overrides[devices.get_config] = get_config

app.dependency_overrides[lights.get_dfr0971_manager] = get_dfr0971_manager
app.dependency_overrides[lights.get_config] = get_config
app.dependency_overrides[lights.get_relay_manager] = get_relay_manager
app.dependency_overrides[lights.get_interlock_manager] = get_interlock_manager
app.dependency_overrides[lights.get_database] = get_database

# Register routes
app.include_router(status.router)
app.include_router(devices.router)
app.include_router(setpoints.router)
app.include_router(schedules.router)
app.include_router(rules.router)
app.include_router(pid.router)
app.include_router(mode.router)
app.include_router(failsafe.router)
app.include_router(alarms.router)
app.include_router(lights.router)
app.add_websocket_route("/ws", websocket.websocket_endpoint)

# Serve static frontend files
# Frontend is in Infrastructure/frontend/dist (sibling to automation-service)
# Try multiple path resolution methods
_base_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
frontend_dist_path = os.path.abspath(os.path.join(_base_path, "frontend", "dist"))

logger.info(f"Frontend dist path: {frontend_dist_path}, exists: {os.path.exists(frontend_dist_path)}")
if os.path.exists(frontend_dist_path):
    # Mount static assets (JS, CSS, images) - these are in the assets subdirectory
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist_path, "assets")), name="static-assets")
    
    # Serve logo.png - must be registered before catch-all route
    logo_path = os.path.join(frontend_dist_path, "logo.png")
    # Use absolute path to ensure it works
    logo_path = os.path.abspath(logo_path)
    logger.info(f"Registering /logo.png route, path: {logo_path}, exists: {os.path.exists(logo_path)}")
    if os.path.exists(logo_path):
        @app.api_route("/logo.png", methods=["GET", "HEAD"], name="logo")
        async def serve_logo():
            """Serve logo.png favicon."""
            logger.info(f"Serving logo from: {logo_path}")
            return FileResponse(logo_path, media_type="image/png")
    else:
        logger.warning(f"Logo file not found at: {logo_path}")
    
    # Serve index.html for root and all non-API routes (SPA fallback)
    @app.get("/")
    async def serve_frontend():
        """Serve frontend index.html."""
        index_path = os.path.join(frontend_dist_path, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {
            "service": "Automation Service",
            "version": "1.0.0",
            "status": "running",
            "note": "Frontend not built. Run 'npm run build' in Infrastructure/frontend"
        }
    
    # SPA fallback: serve index.html for all routes that don't match API routes
    # This must be registered last so API routes take precedence
    @app.get("/{path:path}")
    async def serve_frontend_routes(path: str):
        """Serve frontend routes (SPA fallback)."""
        # Serve logo.png immediately if requested
        if path == "logo.png":
            logo_file = os.path.join(frontend_dist_path, "logo.png")
            if os.path.exists(logo_file):
                return FileResponse(logo_file, media_type="image/png")
        
        # Don't serve frontend for API routes, WebSocket, or FastAPI docs
        # FastAPI should match API routes first, but this is a safety check
        if path.startswith("api/") or path.startswith("ws") or path in ["docs", "openapi.json", "redoc"]:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Not found"}, status_code=404)
        
        # Serve other static files from dist root (favicon, etc.)
        if path and '.' in path and not path.startswith('api/') and not path.startswith('ws'):
            static_file_path = os.path.abspath(os.path.join(frontend_dist_path, path))
            if os.path.exists(static_file_path) and os.path.isfile(static_file_path):
                # Determine media type based on extension
                if path.endswith('.png'):
                    return FileResponse(static_file_path, media_type="image/png")
                elif path.endswith('.ico'):
                    return FileResponse(static_file_path, media_type="image/x-icon")
                elif path.endswith('.svg'):
                    return FileResponse(static_file_path, media_type="image/svg+xml")
                elif path.endswith('.jpg') or path.endswith('.jpeg'):
                    return FileResponse(static_file_path, media_type="image/jpeg")
                else:
                    return FileResponse(static_file_path)
        
        # Serve index.html for SPA routes
        index_path = os.path.join(frontend_dist_path, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not found"}
else:
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "Automation Service",
            "version": "1.0.0",
            "status": "running",
            "note": "Frontend not built. Run 'npm run build' in Infrastructure/frontend"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

