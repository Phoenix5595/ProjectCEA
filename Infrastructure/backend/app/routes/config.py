"""Configuration API routes."""
from fastapi import APIRouter
from typing import Dict, Any
from app.config import ConfigLoader

router = APIRouter(prefix="/api/config", tags=["config"])

# Import from dependencies to avoid circular imports
from app.dependencies import get_config_loader


@router.get("", response_model=Dict[str, Any])
async def get_config():
    """Get full dashboard configuration."""
    config_loader = get_config_loader()
    # Return the full config dict
    import yaml
    with open(config_loader.config_path, 'r') as f:
        return yaml.safe_load(f)


@router.get("/locations")
async def get_locations():
    """Get list of available locations."""
    config_loader = get_config_loader()
    locations = config_loader.get_locations()
    return {"locations": locations}

