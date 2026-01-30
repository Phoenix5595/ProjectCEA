"""Hardware endpoints: MCP23017 relay test and state."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.control.relay_manager import RelayManager

router = APIRouter()


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


class RelayTestRequest(BaseModel):
    """Request body for POST /api/hardware/relays/test."""

    model_config = ConfigDict(populate_by_name=True)

    channel: int | None = None  # Single channel 0-15
    test_all: bool = Field(default=False, alias="all")  # Test all 16 channels
    duration_ms: int = 200  # Time channel is ON before read-back


@router.post("/api/hardware/relays/test")
async def relay_test(
    body: RelayTestRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Run relay channel test: toggle each channel, read back, report pass/fail.

    Commissioning endpoint: briefly turns relays ON then OFF. Use single channel
    or all. In simulation mode runs the same flow and reports mcp_connected: false.
    """
    if body.channel is not None:
        if body.channel < 0 or body.channel > 15:
            raise HTTPException(
                status_code=400,
                detail="channel must be 0-15",
            )
        channels_to_test = [body.channel]
    elif body.test_all:
        channels_to_test = list(range(16))
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide 'channel' (0-15) or 'all': true",
        )

    duration_s = max(0.05, min(2000, body.duration_ms) / 1000.0)
    mcp = relay_manager.mcp23017
    results: list[dict[str, Any]] = []

    for ch in channels_to_test:
        # Set ON, wait, read back
        mcp.set_channel(ch, True)
        await asyncio.sleep(duration_s)
        read_on = mcp.get_channel(ch)
        ok_on = read_on is True

        # Set OFF, short wait, read back
        mcp.set_channel(ch, False)
        await asyncio.sleep(0.05)
        read_off = mcp.get_channel(ch)
        ok_off = read_off is False

        results.append({"channel": ch, "ok": ok_on and ok_off})

    return {
        "results": results,
        "mcp_connected": mcp.is_connected(),
    }


@router.get("/api/hardware/relays/state")
async def relay_state(
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Get current state of all 16 MCP23017 relay channels (True=ON, False=OFF)."""
    mcp = relay_manager.mcp23017
    states = mcp.get_all_channels()
    return {
        "channels": [bool(s) for s in states],
        "mcp_connected": mcp.is_connected(),
        "simulation": mcp.simulation,
    }
