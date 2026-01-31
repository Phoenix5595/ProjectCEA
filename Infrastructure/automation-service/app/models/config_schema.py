from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeviceType(str, Enum):
    RELAY = "relay"
    DIMMING = "dimming"
    SENSOR = "sensor"


class DFR0971Board(BaseModel):
    reference: str = Field(..., description="Dimming board reference")

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("reference must be a non-empty string")
        if not v.upper().startswith("DFR0971"):
            raise ValueError("invalid dimming board reference")
        return v


class HardwareConfig(BaseModel):
    boards: list[DFR0971Board] = Field(default_factory=list)


class DeviceConfig(BaseModel):
    device_type: DeviceType
    channel: int
    dimming_board: str | None = None

    @field_validator("channel")
    @classmethod
    def channel_range(cls, v: int) -> int:
        if not (0 <= v <= 15):
            raise ValueError("channel must be between 0 and 15 inclusive")
        return v


class AutomationConfig(BaseModel):
    relay_channels: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    @classmethod
    def no_duplicate_relays(cls, values):
        channels = values.get("relay_channels", [])
        if len(set(channels)) != len(channels):
            raise ValueError("duplicate relay channels are not allowed")
        return values


class AppConfig(BaseModel):
    # permissive top-level keys to accommodate varied YAML shapes in fixtures
    devices: Any | None = None
    hardware: dict | None = None
    automation: dict | None = None
    control: dict | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def validate_structure(cls, values):
        devices = values.get("devices")
        hardware = values.get("hardware", {}) or {}

        # Validate I2C bus numbers (Raspberry Pi typically has bus 0 and 1)
        for key in ("i2c_bus", "mcp_i2c_bus", "dfr0971_i2c_bus"):
            bus = hardware.get(key)
            if bus is not None and isinstance(bus, int) and (bus < 0 or bus > 7):
                raise ValueError(f"hardware.{key} must be between 0 and 7 (got {bus})")

        # Control loop interval: max 5 seconds (non-negotiable)
        control = values.get("control") or {}
        ui = control.get("update_interval")
        if ui is not None and isinstance(ui, (int, float)):
            if ui < 1 or ui > 5:
                raise ValueError(
                    f"control.update_interval must be between 1 and 5 seconds (got {ui})"
                )

        # Determine known board ids from hardware list variants
        board_ids = set()
        for key in ("boards", "dfr0971_boards", "DFR0971_boards", "dfr_boards"):
            if isinstance(hardware, dict) and key in hardware:
                items = hardware.get(key) or []
                if isinstance(items, list):
                    for b in items:
                        if isinstance(b, dict):
                            board_ids.add(b.get("board_id"))
                            board_ids.add(b.get("id"))

        duplicates = False

        def iter_device_props(devices_dict):
            """Yield each device's props dict. Structure: devices[room][cluster][device_name] = props."""
            if not isinstance(devices_dict, dict):
                return
            for _room_key, room_val in devices_dict.items():
                if not isinstance(room_val, dict):
                    continue
                for _cluster_key, cluster_val in room_val.items():
                    if not isinstance(cluster_val, dict):
                        continue
                    for _dev_name, props in cluster_val.items():
                        if isinstance(props, dict):
                            yield props

        if isinstance(devices, dict):
            for room_key, room_val in devices.items():
                room_channels_seen = set()
                for props in iter_device_props({room_key: room_val}):
                    ch = props.get("channel")
                    if isinstance(ch, int):
                        if ch < 0 or ch > 15:
                            raise ValueError(
                                f"hardware channel must be between 0 and 15 (got {ch})"
                            )
                        if ch in room_channels_seen:
                            duplicates = True
                        room_channels_seen.add(ch)
                    dt = props.get("device_type")
                    if dt is not None:
                        allowed = {
                            "fan",
                            "heater",
                            "light",
                            "dehumidifier",
                            "humidifier",
                            "co2",
                            "vent",
                            "relay",
                            "sensor",
                            "output",
                            "input",
                        }
                        if str(dt) not in allowed:
                            raise ValueError("invalid device_type")
                    for board_key in ("dimming_board_id", "dimming_board_id_ref", "dimming_board"):
                        if board_key in props:
                            bid = props.get(board_key)
                            if isinstance(bid, int) and board_ids and bid not in board_ids:
                                raise ValueError("invalid dimming board reference")

        if duplicates:
            raise ValueError("duplicate relay channels are not allowed")

        return values
