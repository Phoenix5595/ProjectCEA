"""Pydantic schemas for system configuration API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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


class ConfigUpdateRequest(BaseModel):
    """Request model for hardware-only system config updates."""

    model_config = ConfigDict(extra="forbid")

    hardware: HardwareGroup | None = None
