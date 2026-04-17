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
        control = values.get("control") or {}
        allow_legacy_flower_main = bool(control.get("allow_legacy_flower_main", False))

        # Validate I2C bus numbers (Raspberry Pi typically has bus 0 and 1)
        for key in ("i2c_bus", "mcp_i2c_bus", "dfr0971_i2c_bus"):
            bus = hardware.get(key)
            if bus is not None and isinstance(bus, int) and (bus < 0 or bus > 7):
                raise ValueError(f"hardware.{key} must be between 0 and 7 (got {bus})")

        # Control loop interval: max 5 seconds (non-negotiable)
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
            flower_devices = devices.get("Flower Room")
            if isinstance(flower_devices, dict) and not allow_legacy_flower_main:
                # Canonical: all Flower equipment under `main`. `front`/`back` exist only under
                # `sensors:` for dual sensor clusters (not device namespaces).
                has_main = "main" in flower_devices
                legacy_dual = "front" in flower_devices and "back" in flower_devices
                if has_main:
                    for legacy_key in ("front", "back"):
                        legacy = flower_devices.get(legacy_key)
                        if isinstance(legacy, dict) and len(legacy) > 0:
                            raise ValueError(
                                "Flower Room devices: all equipment must be under 'main' only; "
                                f"remove devices from '{legacy_key}' (use 'sensors' for front/back)"
                            )
                elif legacy_dual:
                    pass
                else:
                    raise ValueError(
                        "Flower Room devices must define 'main' (all equipment) "
                        "or legacy both 'front' and 'back' keys"
                    )

            dfr_channels_seen: set[tuple[int, int]] = set()

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

                    if (
                        props.get("dimming_enabled") is True
                        and str(props.get("dimming_type") or "") == "dfr0971"
                    ):
                        bid = props.get("dimming_board_id")
                        ch = props.get("dimming_channel")
                        if bid is None or ch is None:
                            continue
                        if not isinstance(ch, int) or ch not in (0, 1):
                            raise ValueError("invalid dimming_channel for dfr0971 (must be 0 or 1)")
                        if not isinstance(bid, int):
                            raise ValueError("invalid dimming_board_id type (must be int)")
                        key = (bid, ch)
                        if key in dfr_channels_seen:
                            raise ValueError(
                                "duplicate DFR0971 dimming channels are not allowed "
                                f"(board_id={bid} channel={ch})"
                            )
                        dfr_channels_seen.add(key)

        if duplicates:
            raise ValueError("duplicate relay channels are not allowed")

        sensors = values.get("sensors")
        if isinstance(sensors, dict) and not allow_legacy_flower_main:
            flower_sensors = sensors.get("Flower Room")
            if isinstance(flower_sensors, dict):
                missing_fb = [
                    cluster_name
                    for cluster_name in ("front", "back")
                    if cluster_name not in flower_sensors
                ]
                if missing_fb:
                    raise ValueError(
                        "Flower Room sensors must define both 'front' and 'back' clusters "
                        f"(missing: {', '.join(missing_fb)})"
                    )
                flower_devices = devices.get("Flower Room") if isinstance(devices, dict) else None
                if isinstance(flower_devices, dict) and "main" in flower_devices:
                    if "main" not in flower_sensors:
                        raise ValueError(
                            "Flower Room sensors must include 'main' when devices use 'main' "
                            "(control-loop PVs; typically same sensors as 'back')"
                        )

        default_setpoints = control.get("default_setpoints")
        if isinstance(default_setpoints, dict) and not allow_legacy_flower_main:
            flower_setpoints = default_setpoints.get("Flower Room")
            flower_devices = devices.get("Flower Room") if isinstance(devices, dict) else None
            if isinstance(flower_setpoints, dict) and isinstance(flower_devices, dict):
                if "main" in flower_devices:
                    if "main" not in flower_setpoints:
                        raise ValueError(
                            "Flower Room default_setpoints must include 'main' when devices use 'main'"
                        )
                elif "front" in flower_devices and "back" in flower_devices:
                    missing = [
                        cluster_name
                        for cluster_name in ("front", "back")
                        if cluster_name not in flower_setpoints
                    ]
                    if missing:
                        raise ValueError(
                            "Flower Room default_setpoints (legacy) must define both 'front' and "
                            f"'back' clusters (missing: {', '.join(missing)})"
                        )

        return values
