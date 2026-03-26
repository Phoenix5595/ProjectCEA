"""Alarm management schemas."""

from __future__ import annotations

from pydantic import BaseModel


class AlarmAcknowledgeRequest(BaseModel):
    """Request to acknowledge an alarm."""

    pass  # No additional fields needed
