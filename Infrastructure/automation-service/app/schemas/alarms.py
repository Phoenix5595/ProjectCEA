"""Alarm management schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlarmAcknowledgeRequest(BaseModel):
    """Request to acknowledge an alarm."""

    pass  # No additional fields needed


class ActiveAlarmResponse(BaseModel):
    """One active durable alarm exposed to operators."""

    model_config = ConfigDict(frozen=True)

    location: str
    cluster: str
    alarm_name: str
    severity: str
    message: str
    active: bool
    acknowledged: bool
    opened_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None


class AlarmListResponse(BaseModel):
    """Sorted active alarms for a room or the complete facility."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    alarms: tuple[ActiveAlarmResponse, ...]


class AlarmAcknowledgeResponse(BaseModel):
    """Recognition result for one alarm."""

    model_config = ConfigDict(frozen=True)

    location: str
    cluster: str
    alarm_name: str
    acknowledged: bool
    success: bool
