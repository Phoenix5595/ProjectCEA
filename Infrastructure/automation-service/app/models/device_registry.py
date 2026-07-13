"""Pydantic domain models for the device registry.

These models define the typed domain for DB-backed device configuration.
They are used by the DeviceRegistryRepository and the control loop.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

_UI_TO_DB_DEVICE_TYPES: dict[str, str] = {
    "heater": "heating",
    "dehumidifier": "dehumidifier",
    "extraction fan": "exhaust",
    "fan": "cooling",
    "humidifier": "humidifier",
    "co2 tank": "co2",
    "light": "light",
}


class Device(BaseModel):
    """Generic non-light device model.

    Represents heaters, fans, dehumidifiers, CO2, exhaust, etc.
    """

    device_id: int | None = Field(default=None, description="Primary key from device_registry")
    device_type: str
    channel: int = Field(ge=0, le=15, description="MCP23017 relay channel (0-15)")
    pid_enabled: bool = False
    interlock_with: list[str] = []
    pid_setpoints: dict[str, int] = {}
    display_name: str | None = None
    device_name: str = Field(
        pattern=r"^[a-z][a-z0-9]*_[fvlo]_\d+$",
        min_length=1,
        description="Canonical name: <type>_<room_prefix>_<index>",
    )
    location: str
    cluster: Literal["main"] = "main"


class LightDevice(BaseModel):
    """Light device model.

    Light identity is anchored on the DFR0971 dimming board
    (board_id, dimming_channel).  Relay binding is optional
    (relay_channel may be None when unbound).
    """

    device_id: int | None = Field(default=None, description="Primary key from device_registry")
    device_type: Literal["light"] = "light"
    board_id: int | None = Field(
        default=None, ge=0, description="DFR0971 board identifier (0, 1, 2)"
    )
    dimming_channel: int | None = Field(
        default=None, ge=0, le=1, description="DFR0971 channel on the board (0 or 1)"
    )
    dimming_enabled: bool = True
    dimming_type: Literal["dfr0971"] = "dfr0971"
    safety_level: int = 0
    per_room_index: int = Field(ge=1, description="1-based index within the room")
    relay_channel: int | None = Field(default=None, description="MCP23017 relay channel when bound")
    display_name: str
    device_name: str = Field(
        pattern=r"^light_[fvlo]_\d+$",
        description="Canonical name: light_<room_prefix>_<index>",
    )
    location: str
    cluster: Literal["main"] = "main"


class LightDeviceCreate(BaseModel):
    """Request body for creating a new light device on an empty DFR slot."""

    board_id: int = Field(ge=0, description="DFR0971 board identifier")
    dimming_channel: int = Field(ge=0, le=1, description="DFR0971 channel (0 or 1)")
    room: str = Field(description="Room location (e.g. 'Flower Room')")
    display_name: str = Field(description="Human-readable name")
    per_room_index: int | None = Field(
        default=None,
        ge=1,
        description="1-based index within the room; auto-suggested as max+1 when omitted",
    )


class LightDeviceUpdate(BaseModel):
    """Request body for updating an existing light device."""

    display_name: str | None = Field(default=None, description="New human-readable name")
    room: str | None = Field(default=None, description="New room location")
    per_room_index: int | None = Field(
        default=None, ge=1, description="New 1-based index within the room"
    )
    relay_channel: int | None = Field(
        default=None,
        description="Bind to relay channel (set to None to unbind)",
    )
    safety_level: int | None = Field(
        default=None, ge=0, le=100, description="Safety intensity level (0-100%)"
    )
    board_id: int | None = Field(
        default=None, ge=0, description="DFR0971 board identifier (set to None to unbind)"
    )
    dimming_channel: int | None = Field(
        default=None,
        ge=0,
        le=1,
        description="DFR0971 channel on the board (0 or 1, set to None to unbind)",
    )


class DeviceCreate(BaseModel):
    """Request body for creating a new non-light device."""

    device_type: str = Field(description="UI device type (e.g. 'heater', 'fan')")
    room: str = Field(description="Room location (e.g. 'Flower Room')")
    display_name: str = Field(description="Human-readable name")
    channel: int = Field(ge=0, le=15, description="MCP23017 relay channel (0-15)")
    pid_enabled: bool = Field(default=False, description="Enable PID control")
    interlock_with: list[str] = Field(default=[], description="Devices to interlock with")
    pid_setpoints: dict[str, int] = Field(default={}, description="PID setpoint priorities")


class DeviceUpdate(BaseModel):
    """Request body for updating an existing non-light device.

    Room is NOT updatable — device identity is tied to location.
    """

    channel: int | None = Field(default=None, ge=0, le=15, description="New relay channel (0-15)")
    display_name: str | None = Field(default=None, description="New human-readable name")
    pid_enabled: bool | None = Field(default=None, description="Enable/disable PID control")
    interlock_with: list[str] | None = Field(default=None, description="Devices to interlock with")
    pid_setpoints: dict[str, int] | None = Field(
        default=None, description="PID setpoint priorities"
    )
