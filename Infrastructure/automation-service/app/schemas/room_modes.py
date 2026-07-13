"""Room mode and parameters schemas."""

from __future__ import annotations

from pydantic import BaseModel


class RoomMode(BaseModel):
    """Room mode definition."""

    id: int
    name: str
    description: str | None = None
    photoperiod_hours: int | None = None
    is_constant: bool = False


class FlowerSubmode(BaseModel):
    """Flower room submode definition."""

    id: int
    name: str
    description: str | None = None
    week_start: int | None = None
    week_end: int | None = None


class ActiveModeResponse(BaseModel):
    """Response model for active room mode."""

    location: str
    cluster: str
    mode_name: str
    submode_name: str | None = None
    mode_id: int | None = None
    submode_id: int | None = None


class ModeParameters(BaseModel):
    """Full mode parameters for room mode configuration (photoperiod/light only)."""

    day_start_time: str = "17:00"
    night_start_time: str = "11:00"
    light_ramp_up_minutes: int = 15
    light_ramp_down_minutes: int = 15
    main_light_intensity: int = 100  # DEPRECATED: use light_target_intensity table
    supplemental_light_intensity: int = 0  # DEPRECATED: use light_target_intensity table


class RoomModeWithParams(BaseModel):
    """Room mode with full parameters."""

    location: str
    cluster: str
    mode_name: str
    submode_name: str | None = None
    mode_id: int | None = None
    submode_id: int | None = None
    is_constant: bool = False
    parameters: ModeParameters


class SetModeRequest(BaseModel):
    """Request model for setting room mode."""

    mode_name: str
    submode_name: str | None = None
    coordinate_clusters: bool = True  # When True, switch all clusters in location together


class UpdateParametersRequest(BaseModel):
    """Request model for updating mode parameters (photoperiod/light only)."""

    day_start_time: str | None = None
    night_start_time: str | None = None
    light_ramp_up_minutes: int | None = None
    light_ramp_down_minutes: int | None = None
    main_light_intensity: int | None = None  # DEPRECATED: use light_target_intensity table
    supplemental_light_intensity: int | None = None  # DEPRECATED: use light_target_intensity table
