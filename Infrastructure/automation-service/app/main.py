"""Main FastAPI application for automation service."""
from shared.logging import get_logger
from fastapi import FastAPI

from app.bootstrap import lifespan_manager
from app.container import ServiceContainer
from app.middleware import setup_cors, setup_static_files
from app.routes import register_routes, setup_dependency_overrides
from shared.logging import setup_structured_logging

# Configure structured logging
logger = setup_structured_logging(
    service_name="automation-service",
    log_level="INFO",
    console_output=True,
    json_format=True
)

# Global service container
container = ServiceContainer()

# Create FastAPI app
app = FastAPI(
    title="Automation Service",
    description="Device control and automation service for CEA system",
    version="1.0.0",
    lifespan=lambda app: lifespan_manager(app, container)
)

# Setup middleware
setup_cors(app)

# Setup routes and dependency injection first (before static files to avoid catch-all interception)
register_routes(app)
setup_dependency_overrides(app, container)

# Setup static files (catch-all route must be last)
setup_static_files(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

