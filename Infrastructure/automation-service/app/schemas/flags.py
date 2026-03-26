"""Feature flag schemas."""

from __future__ import annotations

from pydantic import BaseModel


class FlagUpdateRequest(BaseModel):
    """Request model for updating a feature flag."""

    enabled: bool


class FlagResponse(BaseModel):
    """Response model for feature flag with optional previous value."""

    name: str
    enabled: bool
    description: str
    previous_enabled: bool | None = None
