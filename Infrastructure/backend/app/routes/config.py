"""Configuration API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/config", tags=["config"])

# Import from dependencies to avoid circular imports
from app.dependencies import get_config_loader


@router.get("", response_model=dict[str, Any])
async def get_config():
    """Get full dashboard configuration."""
    config_loader = get_config_loader()
    # Return the full config dict
    import yaml

    with open(config_loader.config_path) as f:
        return yaml.safe_load(f)


@router.get("/locations")
async def get_locations():
    """Get list of available locations."""
    config_loader = get_config_loader()
    locations = config_loader.get_locations()
    return {"locations": locations}
