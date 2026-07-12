"""Hardware endpoints: MCP23017 relay test and state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis.schema import RELAY_CHANNELS, RELAY_TIMESTAMPS, relay_raw_override_key
from app.redis_client import AutomationRedisClient
from shared.infra_logging import get_logger

router = APIRouter()

logger = get_logger(__name__)


async def _maybe_update_relay_timestamp(
    automation_redis: AutomationRedisClient,
    channel: int,
    old_state: bool,
    new_state: bool,
) -> None:
    """Update RELAY_TIMESTAMPS[channel] only when the state actually changed."""
    if old_state == new_state:
        return
    if automation_redis.redis_client is None:
        return
    try:
        raw_ts = automation_redis.get(RELAY_TIMESTAMPS)
        timestamps: list[str | None] = json.loads(raw_ts) if raw_ts else [None] * 16
        now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        timestamps[channel] = now_iso
        await asyncio.to_thread(
            automation_redis.redis_client.set,
            RELAY_TIMESTAMPS,
            json.dumps(timestamps),
        )
    except Exception as e:
        logger.warning(f"Failed to update RELAY_TIMESTAMPS for channel {channel}: {e}")


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


class RelayChannelControlRequest(BaseModel):
    """Request body for POST /api/hardware/relays/channel/{channel}/state."""

    model_config = ConfigDict(populate_by_name=True)

    state: int = Field(ge=0, le=1)  # 0 = OFF, 1 = ON
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)


@router.post("/api/hardware/relays/channel/{channel}/state")
async def set_relay_channel_state(
    channel: int,
    body: RelayChannelControlRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    automation_redis: AutomationRedisClient = Depends(get_automation_redis),
) -> dict[str, Any]:
    """Set a single relay channel ON or OFF directly (raw control).

    Bypasses device mapping—useful for commissioning or controlling
    unassigned channels. Channel must be 0-15.
    """
    if channel < 0 or channel > 15:
        raise HTTPException(
            status_code=400,
            detail="channel must be 0-15",
        )

    # Read current channel state before hardware write so we can detect
    # actual state changes and update RELAY_TIMESTAMPS only when needed.
    old_state = False
    try:
        raw_channels = automation_redis.get(RELAY_CHANNELS)
        if raw_channels is not None:
            parsed_channels = json.loads(raw_channels)
            if isinstance(parsed_channels, list) and len(parsed_channels) == 16:
                old_state = bool(parsed_channels[channel])
    except (json.JSONDecodeError, ValueError, ConnectionError):
        old_state = False

    override_key = relay_raw_override_key(channel)

    if body.state == 1:
        if body.duration_seconds is not None:
            expires_at = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)
            success = await relay_manager.set_channel_state(channel, 1)
            if not success:
                raise HTTPException(
                    status_code=503,
                    detail=f"Failed to set channel {channel} to ON",
                )
            await _maybe_update_relay_timestamp(automation_redis, channel, old_state, True)
            if automation_redis.redis_client is not None:
                await asyncio.to_thread(
                    automation_redis.redis_client.setex,
                    override_key,
                    body.duration_seconds + 86400,
                    json.dumps({"expires_at": expires_at.isoformat(), "state": 1}),
                )
        else:
            if automation_redis.redis_client is not None:
                await asyncio.to_thread(
                    automation_redis.redis_client.delete,
                    override_key,
                )
            success = await relay_manager.set_channel_state(channel, 1)
            if not success:
                raise HTTPException(
                    status_code=503,
                    detail=f"Failed to set channel {channel} to ON",
                )
            await _maybe_update_relay_timestamp(automation_redis, channel, old_state, True)
    else:
        if automation_redis.redis_client is not None:
            await asyncio.to_thread(
                automation_redis.redis_client.delete,
                override_key,
            )
        success = await relay_manager.set_channel_state(channel, 0)
        if not success:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to set channel {channel} to OFF",
            )
        await _maybe_update_relay_timestamp(automation_redis, channel, old_state, False)

    return {
        "channel": channel,
        "state": body.state,
        "ok": True,
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

    override_expires_at: list[str | None] = [None] * 16
    try:
        if automation_redis.redis_client is not None:
            override_keys = [relay_raw_override_key(ch) for ch in range(16)]
            raw_overrides = await asyncio.to_thread(
                automation_redis.redis_client.mget, override_keys
            )
            now = datetime.now(UTC)
            for i, raw in enumerate(raw_overrides or []):  # type: ignore[arg-type]
                if raw is not None:
                    try:
                        data = json.loads(raw)
                        expires_at_str = data.get("expires_at")
                        if expires_at_str:
                            expires_at = datetime.fromisoformat(expires_at_str)
                            if expires_at > now:
                                override_expires_at[i] = expires_at_str
                    except (json.JSONDecodeError, ValueError):
                        pass
    except Exception as e:
        logger.error(f"Failed to read relay override keys: {e}", exc_info=True)

    modes: list[str | None] = ["off"] * 16
    try:
        device_states = await database.device_repo.get_all_device_states()
        channel_to_mode: dict[int, str] = {}
        for ds in device_states:
            ch = ds.get("channel")
            if isinstance(ch, int) and 0 <= ch <= 15 and ch not in channel_to_mode:
                channel_to_mode[ch] = ds.get("mode", "auto")
        for ch in range(16):
            if override_expires_at[ch] is not None:
                modes[ch] = "manual"
            elif ch in channel_to_mode:
                modes[ch] = channel_to_mode[ch]
            else:
                modes[ch] = "off"
    except Exception as e:
        logger.error(f"Failed to read device states for mode mapping: {e}", exc_info=True)

    return {
        "channels": channels,
        "timestamps": timestamps,
        "mcp_connected": mcp_connected,
        "modes": modes,
        "override_expires_at": override_expires_at,
    }
