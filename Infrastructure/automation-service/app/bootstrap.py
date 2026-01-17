"""Bootstrap utilities for application startup and shutdown."""
from __future__ import annotations

from shared.logging import get_logger
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI
from app.container import ServiceContainer

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan_manager(app: FastAPI, container: ServiceContainer):
    """Application lifespan manager using service container."""
    # Startup
    logger.info("Starting automation service...")
    try:
        await container.initialize()
        logger.info("Automation service started successfully")
        yield
    except Exception as e:
        logger.error(f"Failed to start automation service: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        await container.shutdown()
        logger.info("Automation service stopped")