"""Status and health check routes."""
from fastapi import APIRouter, Depends
from typing import Dict, Any
from app.database import DatabaseManager

router = APIRouter()

# Dependency injection (will be overridden in main.py)
def get_database() -> DatabaseManager:
    """Get database manager."""
    raise NotImplementedError("Dependency not injected")


@router.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint."""
    return {
        "service": "Soil Sensor Service",
        "version": "1.0.0",
        "status": "running"
    }


@router.get("/health")
async def health() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


@router.get("/status")
async def status(db: DatabaseManager = Depends(get_database)) -> Dict[str, Any]:
    """Detailed status endpoint."""
    return {
        "service": "Soil Sensor Service",
        "version": "1.0.0",
        "database_connected": db._db_connected,
        "status": "running"
    }

