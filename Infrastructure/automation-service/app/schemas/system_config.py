"""Pydantic schemas for system configuration API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DfrBoardUpdate(BaseModel):
    """DFR0971 board entry for hardware config."""

    board_id: int
    i2c_address: int
    name: str


class HardwareGroup(BaseModel):
    """Hardware configuration group."""

    i2c_bus: int | None = None
    mcp_i2c_bus: int | None = None
    dfr0971_i2c_bus: int | None = None
    i2c_address: int | None = None
    simulation: bool | None = None
    active_low: bool | None = None
    require_mcp: bool | None = None
    dfr0971_boards: list[DfrBoardUpdate] | None = None


class PidLimitsPair(BaseModel):
    """PID limit pair for a single device type."""

    kp_min: float
    kp_max: float
    ki_min: float
    ki_max: float
    kd_min: float
    kd_max: float

    @model_validator(mode="after")
    def check_min_max(self):
        if self.kp_min < 0:
            raise ValueError("kp_min must be non-negative")
        if self.kp_max < 0:
            raise ValueError("kp_max must be non-negative")
        if self.ki_min < 0:
            raise ValueError("ki_min must be non-negative")
        if self.ki_max < 0:
            raise ValueError("ki_max must be non-negative")
        if self.kd_min < 0:
            raise ValueError("kd_min must be non-negative")
        if self.kd_max < 0:
            raise ValueError("kd_max must be non-negative")
        if self.kp_min > self.kp_max:
            raise ValueError("kp_min must be <= kp_max")
        if self.ki_min > self.ki_max:
            raise ValueError("ki_min must be <= ki_max")
        if self.kd_min > self.kd_max:
            raise ValueError("kd_min must be <= kd_max")
        return self


class PidLimitsGroup(BaseModel):
    """PID limits for all device types."""

    heater: PidLimitsPair | None = None
    fan: PidLimitsPair | None = None
    co2: PidLimitsPair | None = None


class SafetyLimitsGroup(BaseModel):
    """Safety limits group with min/max validators."""

    min_temperature: float | None = None
    max_temperature: float | None = None
    min_humidity: float | None = None
    max_humidity: float | None = None
    min_co2: float | None = None
    max_co2: float | None = None

    @model_validator(mode="after")
    def check_ranges(self):
        if self.min_temperature is not None and self.max_temperature is not None:
            if self.min_temperature >= self.max_temperature:
                raise ValueError("min_temperature must be < max_temperature")
        if self.min_humidity is not None and self.max_humidity is not None:
            if self.min_humidity >= self.max_humidity:
                raise ValueError("min_humidity must be < max_humidity")
        if self.min_co2 is not None and self.max_co2 is not None:
            if self.min_co2 >= self.max_co2:
                raise ValueError("min_co2 must be < max_co2")
        return self


class TuningGroup(BaseModel):
    """Control tuning parameters."""

    update_interval: int | None = Field(None, ge=1, le=5)
    last_good_hold_period: int | None = None
    binary_hysteresis: float | None = None
    pid_limits: PidLimitsGroup | None = None


class ConfigUpdateRequest(BaseModel):
    """Request model for system config update."""

    model_config = ConfigDict(extra="forbid")

    hardware: HardwareGroup | None = None
    safety_limits: SafetyLimitsGroup | None = None
    tuning: TuningGroup | None = None
