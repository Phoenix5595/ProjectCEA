"""Explicit API models for the on-demand control snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class DeviceAssignmentResponse(BaseModel):
    """Registry identity and preserved schedule metadata for one assigned device."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    location: str
    cluster: str
    device_name: str
    display_name: str | None
    device_type: str | None
    inherited_schedule_count: int
    inherited_schedule_summary: str | None


class HardwareAlarmResponse(BaseModel):
    """An active hardware-related alarm held by the alarm manager."""

    model_config = ConfigDict(frozen=True)

    location: str
    cluster: str
    alarm_name: str
    severity: str
    message: str


class RelayControlSnapshotResponse(BaseModel):
    """Observed and desired facts for one physical relay."""

    model_config = ConfigDict(frozen=True)

    physical_relay: int
    channel: int
    pin_label: str
    observed_state: bool | None
    changed_at: datetime | None
    assignment: DeviceAssignmentResponse | None
    desired_state: int | None
    command_mode: str | None
    command_expires_at: datetime | None
    prior_command_mode: str | None
    syncing: bool
    stale: bool
    last_command_succeeded: bool | None
    recovery_pending: bool
    alarm: HardwareAlarmResponse | None


class DfrChannelSnapshotResponse(BaseModel):
    """Availability and commanded-value cache for one DFR output slot."""

    model_config = ConfigDict(frozen=True)

    channel: int
    available: bool
    assignment: DeviceAssignmentResponse | None
    commanded_intensity: float | None
    command_acknowledged: bool


class DfrBoardSnapshotResponse(BaseModel):
    """One DFR board's public, address-free output slots."""

    model_config = ConfigDict(frozen=True)

    board_id: int
    available: bool
    channels: tuple[DfrChannelSnapshotResponse, ...]


class ControlSnapshotResponse(BaseModel):
    """Complete atomic-ish read projection from the control state owners."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    registry_version: int
    sampled_at: datetime | None
    freshness: Literal["FRESH", "STALE"]
    stale_since: datetime | None
    relays: tuple[RelayControlSnapshotResponse, ...]
    dfr_boards: tuple[DfrBoardSnapshotResponse, ...]
    hardware_alarms: tuple[HardwareAlarmResponse, ...]
