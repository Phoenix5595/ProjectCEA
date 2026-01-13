"""Route registration and dependency injection setup."""
from shared.logging import get_logger
from fastapi import FastAPI

from app.routes import schedules, lights, setpoints, devices, status, alarms, pid, mode, rules, failsafe, websocket, redis_state

logger = get_logger(__name__)


def register_routes(app: FastAPI) -> None:
    """Register all API routes with the FastAPI application.
    
    Args:
        app: FastAPI application instance
    """
    # API Routes
    app.include_router(schedules.router, tags=["schedules"])
    app.include_router(lights.router, tags=["lights"])
    app.include_router(setpoints.router, tags=["setpoints"])
    app.include_router(devices.router, tags=["devices"])
    app.include_router(status.router, tags=["status"])
    app.include_router(alarms.router, tags=["alarms"])
    app.include_router(pid.router, tags=["pid"])
    app.include_router(mode.router, tags=["mode"])
    app.include_router(rules.router, tags=["rules"])
    app.include_router(failsafe.router, tags=["failsafe"])
    app.include_router(websocket.router, tags=["websocket"])
    app.include_router(room_modes.router, tags=["room-modes"])
    app.include_router(redis_state.router, tags=["redis-state"])
    
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "healthy", "service": "automation-service"}
    
    logger.info("All routes registered")


def setup_dependency_overrides(app: FastAPI, container) -> None:
    """Setup dependency injection overrides for route handlers.
    
    Args:
        app: FastAPI application instance
        container: ServiceContainer instance with initialized dependencies
    """
    # Override dependencies in schedules module
    app.dependency_overrides[schedules.get_database] = container.get_database
    app.dependency_overrides[schedules.get_scheduler] = container.get_scheduler
    app.dependency_overrides[schedules.get_config] = container.get_config
    
    # Override dependencies in lights module
    app.dependency_overrides[lights.get_database] = container.get_database
    app.dependency_overrides[lights.get_config] = container.get_config
    app.dependency_overrides[lights.get_dfr0971_manager] = container.get_dfr0971_manager
    app.dependency_overrides[lights.get_relay_manager] = container.get_relay_manager
    app.dependency_overrides[lights.get_interlock_manager] = container.get_interlock_manager
    app.dependency_overrides[lights.get_scheduler] = container.get_scheduler
    
    # Override dependencies in setpoints module
    app.dependency_overrides[setpoints.get_database] = container.get_database
    app.dependency_overrides[setpoints.get_config] = container.get_config
    
    # Override dependencies in devices module
    app.dependency_overrides[devices.get_database] = container.get_database
    app.dependency_overrides[devices.get_config] = container.get_config
    app.dependency_overrides[devices.get_relay_manager] = container.get_relay_manager
    
    # Override dependencies in status module
    app.dependency_overrides[status.get_database] = container.get_database
    app.dependency_overrides[status.get_config] = container.get_config
    app.dependency_overrides[status.get_relay_manager] = container.get_relay_manager
    
    # Override dependencies in rules module
    app.dependency_overrides[rules.get_database] = container.get_database
    
    # Override dependencies in pid module
    app.dependency_overrides[pid.get_database] = container.get_database
    app.dependency_overrides[pid.get_config] = container.get_config
    
    # Override dependencies in mode module  
    # mode.py uses direct imports from app.main, may need refactoring
    
    # Override dependencies in failsafe module
    app.dependency_overrides[failsafe.get_database] = container.get_database
    
    # Override dependencies in redis_state module
    app.dependency_overrides[redis_state.get_database] = container.get_database
    
    logger.info("Dependency overrides configured")
