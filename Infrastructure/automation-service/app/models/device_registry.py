"""Pydantic domain models for the device registry.

These models define the typed domain for DB-backed device configuration.
They are used by the DeviceRegistryRepository and the control loop.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_UI_TO_DB_DEVICE_TYPES: dict[str, str] = {
    "heater": "heating",
    "dehumidifier": "dehumidifier",
    "extraction fan": "exhaust",
    "fan": "exhaust",
    "humidifier": "humidifier",
    "co2 tank": "co2",
    "light": "light",
}

_UI_NON_LIGHT_DEVICE_TYPES = Literal[
    "heater",
    "heating",
    "dehumidifier",
    "fan",
    "extraction fan",
    "exhaust",
    "humidifier",
    "co2 tank",
    "co2",
    "cooling",
]


def normalize_device_type(device_type: str) -> str:
    """Convert a UI actuator label to its physical canonical device type."""
    normalized_type = device_type.strip().lower()
    return _UI_TO_DB_DEVICE_TYPES.get(normalized_type, normalized_type)


class _DisplayNameInput(BaseModel):
    """Common validation for human-facing registry labels."""

    @field_validator("display_name", check_fields=False)
    @classmethod
    def trim_display_name(cls, display_name: str) -> str:
        """Reject blank labels while preserving a normalized human-facing name."""
        trimmed_display_name = display_name.strip()
        if not trimmed_display_name:
            raise ValueError("display_name must not be blank")
        return trimmed_display_name


class Device(BaseModel):
    """Generic non-light device model.

    Represents heaters, fans, dehumidifiers, CO2, exhaust, etc.
    """

    device_id: int | None = Field(default=None, description="Primary key from device_registry")
    device_type: str
    channel: int | None = Field(
        default=None, ge=0, le=15, description="MCP23017 relay channel (0-15), if bound"
    )
    pid_enabled: bool = False
    interlock_with: list[str] = []
    pid_setpoints: dict[str, int] = {}
    display_name: str | None = None
    inherited_schedule_count: int = Field(default=0, ge=0)
    inherited_schedule_summary: list[str] = Field(default_factory=list)
    device_name: str = Field(
        min_length=1,
        description="Canonical or legacy device name (canonical enforced at creation).",
    )
    location: str
    cluster: Literal["main"] = "main"

    @field_validator("device_name")
    @classmethod
    def _device_name_not_blank(cls, device_name: str) -> str:
        """Allow legacy names while rejecting blank values."""
        stripped_device_name = device_name.strip()
        if not stripped_device_name:
            raise ValueError("device_name must not be blank")
        return stripped_device_name


class LightDevice(BaseModel):
    """Light device model.

    Light identity is anchored on the DFR0971 dimming board
    (board_id, dimming_channel).  Relay binding is optional
    (relay_channel may be None when unbound).
    """

    device_id: int | None = Field(default=None, description="Primary key from device_registry")
    device_type: Literal["light"] = "light"
    board_id: int = Field(ge=0, le=2, description="DFR0971 board identifier (0, 1, 2)")
    dimming_channel: int = Field(ge=0, le=1, description="DFR0971 channel on the board (0 or 1)")
    dimming_enabled: bool = True
    dimming_type: Literal["dfr0971"] = "dfr0971"
    safety_level: int = 0
    per_room_index: int = Field(ge=1, description="1-based index within the room")
    relay_channel: int | None = Field(
        default=None, ge=0, le=15, description="MCP23017 relay channel when bound"
    )
    display_name: str
    inherited_schedule_count: int = Field(default=0, ge=0)
    inherited_schedule_summary: list[str] = Field(default_factory=list)
    device_name: str = Field(
        min_length=1,
        description="Canonical or legacy light name (canonical enforced at creation).",
    )
    location: str
    cluster: Literal["main"] = "main"

    @field_validator("device_name")
    @classmethod
    def _device_name_not_blank(cls, device_name: str) -> str:
        """Allow legacy names while rejecting blank values."""
        stripped_device_name = device_name.strip()
        if not stripped_device_name:
            raise ValueError("device_name must not be blank")
        return stripped_device_name


class LightDeviceCreate(_DisplayNameInput):
    """Request body for creating a new light device on an empty DFR slot."""

    model_config = ConfigDict(extra="forbid")

    device_type: Literal["light"] = "light"
    board_id: int = Field(ge=0, le=2, description="DFR0971 board identifier (0, 1, 2)")
    dimming_channel: int = Field(ge=0, le=1, description="DFR0971 channel (0 or 1)")
    room: str = Field(description="Room location (e.g. 'Flower Room')")
    display_name: str = Field(description="Human-readable name")
    relay_channel: int | None = Field(
        default=None, ge=0, le=15, description="MCP23017 relay channel when bound"
    )


class LightDeviceUpdate(_DisplayNameInput):
    """Request body for updating an existing light device."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, description="New human-readable name")
    relay_channel: int | None = Field(
        default=None,
        ge=0,
        le=15,
        description="Bind to relay channel (set to None to unbind)",
    )
    board_id: int | None = Field(
        default=None, ge=0, le=2, description="DFR0971 board identifier (0, 1, 2)"
    )
    dimming_channel: int | None = Field(
        default=None,
        ge=0,
        le=1,
        description="DFR0971 channel on the board (0 or 1)",
    )

    @model_validator(mode="after")
    def validate_update_contract(self) -> LightDeviceUpdate:
        """Keep display and DFR identities valid when their fields are supplied."""
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name cannot be null")

        updates_dfr_board = "board_id" in self.model_fields_set
        updates_dfr_channel = "dimming_channel" in self.model_fields_set
        if updates_dfr_board != updates_dfr_channel:
            raise ValueError("board_id and dimming_channel must be updated together")
        if updates_dfr_board and (self.board_id is None or self.dimming_channel is None):
            raise ValueError("lights require a complete DFR board_id and dimming_channel pair")
        return self


class DeviceCreate(_DisplayNameInput):
    """Request body for creating a new non-light device."""

    model_config = ConfigDict(extra="forbid")

    device_type: _UI_NON_LIGHT_DEVICE_TYPES = Field(
        description="UI device type describing the physical actuator"
    )
    room: str = Field(description="Room location (e.g. 'Flower Room')")
    display_name: str = Field(description="Human-readable name")
    channel: int | None = Field(
        default=None, ge=0, le=15, description="MCP23017 relay channel (0-15), if bound"
    )
    pid_enabled: bool = Field(default=False, description="Enable PID control")
    interlock_with: list[str] = Field(default=[], description="Devices to interlock with")
    pid_setpoints: dict[str, int] = Field(default={}, description="PID setpoint priorities")

    @model_validator(mode="after")
    def normalize_input_device_type(self) -> DeviceCreate:
        """Parse UI aliases into the canonical physical actuator type."""
        match normalize_device_type(self.device_type):
            case "heating":
                self.device_type = "heating"
            case "dehumidifier":
                self.device_type = "dehumidifier"
            case "exhaust":
                self.device_type = "exhaust"
            case "humidifier":
                self.device_type = "humidifier"
            case "co2":
                self.device_type = "co2"
            case "cooling":
                self.device_type = "cooling"
            case unsupported_device_type:
                raise ValueError(f"Unsupported device type: {unsupported_device_type}")
        return self


class DeviceUpdate(_DisplayNameInput):
    """Request body for updating an existing non-light device.

    Room is NOT updatable — device identity is tied to location.
    """

    model_config = ConfigDict(extra="forbid")

    channel: int | None = Field(
        default=None, ge=0, le=15, description="New relay channel (0-15, or null to unbind)"
    )
    display_name: str | None = Field(default=None, description="New human-readable name")
    pid_enabled: bool | None = Field(default=None, description="Enable/disable PID control")
    interlock_with: list[str] | None = Field(default=None, description="Devices to interlock with")
    pid_setpoints: dict[str, int] | None = Field(
        default=None, description="PID setpoint priorities"
    )

    @model_validator(mode="after")
    def validate_update_contract(self) -> DeviceUpdate:
        """Reject explicit label removal while allowing explicit relay unbinding."""
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name cannot be null")
        return self


class RegistryDeviceUpdate(_DisplayNameInput):
    """Validated update envelope parsed into a device-kind-specific DTO while locked."""

    model_config = ConfigDict(extra="forbid")

    channel: int | None = Field(default=None, ge=0, le=15)
    relay_channel: int | None = Field(default=None, ge=0, le=15)
    display_name: str | None = None
    pid_enabled: bool | None = None
    interlock_with: list[str] | None = None
    pid_setpoints: dict[str, int] | None = None
    board_id: int | None = Field(default=None, ge=0, le=2)
    dimming_channel: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_display_name(self) -> RegistryDeviceUpdate:
        """Reject explicit label removal before the locked kind-specific parse."""
        if "display_name" in self.model_fields_set and self.display_name is None:
            raise ValueError("display_name cannot be null")
        return self


RegistryDeviceCreate = DeviceCreate | LightDeviceCreate
