"""Configuration API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/config", tags=["config"])

from app.dependencies import get_config_repository  # noqa: E402


@router.get("", response_model=dict[str, Any])
async def get_config():
    """Get full dashboard configuration."""
    config_repo = get_config_repository()
    return config_repo.get_full_config()


@router.get("/locations")
async def get_locations():
    """Get list of available locations."""
    config_repo = get_config_repository()
    locations = config_repo.get_locations()
    return {"locations": locations}
