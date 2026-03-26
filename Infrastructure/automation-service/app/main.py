"""Main FastAPI application for automation service."""

from __future__ import annotations

from fastapi import FastAPI

from app.bootstrap import lifespan_manager
from app.container import ServiceContainer
from app.middleware.exception_handler import exception_handler_middleware
from app.middleware.profiling import profiling_middleware
from app.middleware_utils import setup_cors, setup_static_files
from app.routes import register_routes, setup_dependency_overrides
from shared.infra_logging import setup_structured_logging

# Configure structured logging
logger = setup_structured_logging(
    service_name="automation-service", log_level="INFO", console_output=True, json_format=True
)

# Global service container
container = ServiceContainer()

# Create FastAPI app
app = FastAPI(
    title="Automation Service",
    description="Device control and automation service for CEA (Controlled Environment Agriculture) system. Provides autonomous climate control, device scheduling, and real-time monitoring.",
    version="1.0.0",
    lifespan=lambda app: lifespan_manager(app, container),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={"name": "CEA Automation System", "email": "support@cea.local"},
    license_info={
        "name": "Proprietary",
    },
    tags_metadata=[
        {
            "name": "devices",
            "description": "Device control and management endpoints",
        },
        {
            "name": "schedules",
            "description": "Schedule management for devices and climate control",
        },
        {
            "name": "lights",
            "description": "Light dimming control for DFR0971 modules",
        },
        {
            "name": "rules",
            "description": "Automation rules and logic",
        },
        {
            "name": "pid",
            "description": "PID parameter configuration",
        },
        {
            "name": "alarms",
            "description": "Alarm management and monitoring",
        },
        {
            "name": "status",
            "description": "System status and health checks",
        },
        {
            "name": "websocket",
            "description": "WebSocket endpoints for real-time updates",
        },
    ],
)

# Setup middleware
setup_cors(app)
app.middleware("http")(exception_handler_middleware)
app.middleware("http")(profiling_middleware)

# Setup routes and dependency injection first (before static files to avoid catch-all interception)
register_routes(app)
setup_dependency_overrides(app, container)

# Setup static files (catch-all route must be last)
setup_static_files(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
