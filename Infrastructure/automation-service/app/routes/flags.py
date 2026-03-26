"""Feature flag management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.feature_flags import FeatureFlag, FeatureFlagManager
from app.schemas.flags import FlagResponse, FlagUpdateRequest

router = APIRouter()


# These will be overridden by main app
def get_feature_flag_manager() -> FeatureFlagManager:
    """Dependency to get feature flag manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/flags", response_model=list[FeatureFlag])
async def get_all_flags(
    flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager),
) -> list[FeatureFlag]:
    """Get all feature flags with their current values.

    Returns:
        List of all defined feature flags
    """
    return flag_manager.get_all_flags()


@router.get("/api/flags/{flag_name}", response_model=FeatureFlag)
async def get_flag(
    flag_name: str, flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager)
) -> FeatureFlag:
    """Get a specific feature flag.

    Args:
        flag_name: Name of the feature flag

    Returns:
        Feature flag details

    Raises:
        HTTPException: If flag is not found
    """
    flag = flag_manager.get_flag_definition(flag_name)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"Feature flag '{flag_name}' not found")

    return flag


@router.put("/api/flags/{flag_name}", response_model=FlagResponse)
async def update_flag(
    flag_name: str,
    request: FlagUpdateRequest,
    flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager),
) -> FlagResponse:
    """Update a feature flag.

    Args:
        flag_name: Name of the feature flag
        request: Update request with new enabled value

    Returns:
        Updated flag with previous value

    Raises:
        HTTPException: If flag is not found
    """
    # Check if flag exists
    if flag_name not in flag_manager.FLAG_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Feature flag '{flag_name}' not found")

    # Get previous value for response
    previous_value = flag_manager.get_flag(flag_name)

    # Update flag
    flag_manager.set_flag(flag_name, request.enabled)

    # Get updated flag definition
    updated_flag = flag_manager.get_flag_definition(flag_name)
    if updated_flag is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated flag")

    return FlagResponse(
        name=updated_flag.name,
        enabled=updated_flag.enabled,
        description=updated_flag.description,
        previous_enabled=previous_value,
    )


@router.get("/api/flags/{flag_name}/status")
async def get_flag_status(
    flag_name: str, flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager)
) -> dict[str, Any]:
    """Get simple status of a feature flag (enabled/disabled).

    Args:
        flag_name: Name of the feature flag

    Returns:
        Dictionary with flag status

    Raises:
        HTTPException: If flag is not found
    """
    if flag_name not in flag_manager.FLAG_DEFINITIONS:
        raise HTTPException(status_code=404, detail=f"Feature flag '{flag_name}' not found")

    enabled = flag_manager.get_flag(flag_name)
    return {"name": flag_name, "enabled": enabled}


@router.get("/api/flags/cache/stats")
async def get_cache_stats(
    flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager),
) -> dict[str, Any]:
    """Get cache statistics for monitoring.

    Returns:
        Dictionary with cache statistics
    """
    return flag_manager._get_cache_stats()


@router.post("/api/flags/cache/clear")
async def clear_cache(
    flag_manager: FeatureFlagManager = Depends(get_feature_flag_manager),
) -> dict[str, str]:
    """Clear the local flag cache.

    Returns:
        Success message
    """
    flag_manager.clear_cache()
    return {"message": "Flag cache cleared successfully"}
