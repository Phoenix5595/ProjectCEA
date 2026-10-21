"""Light dimming control schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TargetIntensityControl(BaseModel):
    """Request model for setting target light intensity."""

    target_intensity: float
    expected_mode_id: int | None = Field(
        default=None,
        description="Active mode captured with staged edits. A mismatch returns 409 "
        "light_target_mode_changed without reading or writing targets; omit for legacy live-active behavior.",
    )


class ScheduleTimeControl(BaseModel):
    """Request model for updating light schedule times."""

    start_time: str
    end_time: str


class LightIntensityUpdate(BaseModel):
    """Request model for updating light target intensity by device_id."""

    target_intensity: float
    expected_mode_id: int | None = Field(
        default=None,
        description="Active mode captured with staged edits. A mismatch returns 409 "
        "light_target_mode_changed without reading or writing targets; omit for legacy live-active behavior.",
    )


class DfrChannelAssignControl(BaseModel):
    """Assign/unassign a DFR0971 board/channel to a dimmable light device."""

    location: str
    cluster: str
    device_name: str
    board_id: int | None = None
    dimming_channel: int | None = None
