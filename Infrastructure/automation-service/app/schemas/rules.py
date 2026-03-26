"""Automation rules schemas."""

from __future__ import annotations

from pydantic import BaseModel


class RuleCreate(BaseModel):
    """Request model for creating a rule."""

    name: str
    enabled: bool = True
    location: str
    cluster: str
    condition_sensor: str
    condition_operator: str  # '<', '>', '<=', '>=', '=='
    condition_value: float
    action_device: str
    action_state: int  # 0 = OFF, 1 = ON
    priority: int = 0
    schedule_id: int | None = None


class RuleUpdate(BaseModel):
    """Request model for updating a rule."""

    name: str | None = None
    enabled: bool | None = None
    condition_sensor: str | None = None
    condition_operator: str | None = None
    condition_value: float | None = None
    action_device: str | None = None
    action_state: int | None = None
    priority: int | None = None
    schedule_id: int | None = None


class RuleToggle(BaseModel):
    """Request model for toggling a rule."""

    enabled: bool
