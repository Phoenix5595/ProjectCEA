"""Hardware endpoints: MCP23017 relay test and state."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis.schema import RELAY_CHANNELS, RELAY_TIMESTAMPS
from app.redis_client import AutomationRedisClient
from shared.infra_logging import get_logger

router = APIRouter()

logger = get_logger(__name__)


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_automation_redis() -> AutomationRedisClient:
    """Dependency to get AutomationRedisClient."""
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
    or all. Real hardware is required; ``mcp_connected`` reports probe status.
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
    automation_redis: AutomationRedisClient = Depends(get_automation_redis),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    mcp = relay_manager.mcp23017
    mcp_connected = mcp.is_connected()

    # Try Redis cache first
    channels: list[bool] | None = None
    try:
        raw = automation_redis.get(RELAY_CHANNELS)
        if raw is not None:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) == 16:
                channels = [bool(s) for s in parsed]
    except (json.JSONDecodeError, ValueError, ConnectionError) as e:
        logger.error(f"Failed to parse relay channels from Redis cache: {e}", exc_info=True)
        channels = None

    # Fall back to hardware read on cache miss
    if channels is None:
        states = mcp.get_all_channels()
        channels = [bool(s) for s in states]

    # Read per-channel timestamps from Redis (event-driven, <1ms).
    # Updated by hardware_batch only when a channel actually changes state.
    timestamps: list[str | None] = [None] * 16
    try:
        raw_ts = automation_redis.get(RELAY_TIMESTAMPS)
        if raw_ts is not None:
            parsed_ts = json.loads(raw_ts)
            if isinstance(parsed_ts, list) and len(parsed_ts) == 16:
                timestamps = [str(t) if t is not None else None for t in parsed_ts]
    except (json.JSONDecodeError, ValueError, ConnectionError) as e:
        logger.error(f"Failed to parse relay timestamps from Redis cache: {e}", exc_info=True)

    # Fallback to TimescaleDB only if Redis miss (cold start / stale)
    if all(t is None for t in timestamps):
        try:
            rows = await database.control_action_repo.get_last_changed_per_channel()
            for row in rows:
                ch = row["channel"]
                if 0 <= ch <= 15:
                    timestamps[ch] = row["last_changed"]
        except Exception as e:
            logger.error(f"Failed to fetch last-changed timestamps: {e}", exc_info=True)

    return {
        "channels": channels,
        "timestamps": timestamps,
        "mcp_connected": mcp_connected,
    }
