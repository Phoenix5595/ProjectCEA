"""Bootstrap utilities for application startup and shutdown."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.container import ServiceContainer
from shared.infra_logging import get_logger
from shared.lifespan import notify_started, notify_stopping

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan_manager(app: FastAPI, container: ServiceContainer):
    """Application lifespan manager using service container."""
    # Startup
    logger.info("Starting automation service...")
    try:
        await container.initialize()
        logger.info("Automation service started successfully")
        notify_started("automation-service", logger)
        yield
    except Exception as e:
        logger.error(f"Failed to start automation service: {e}", exc_info=True)
        raise
    finally:
        notify_stopping("automation-service", logger)
        # Shutdown
        await container.shutdown()
        logger.info("Automation service stopped")
