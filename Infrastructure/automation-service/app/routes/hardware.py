"""Hardware endpoints: MCP23017 relay test and state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis.schema import relay_raw_override_key
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


def get_relay_board_state_manager() -> RelayBoardStateManager:
    """Dependency to get the in-process relay board snapshot owner."""
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
    relay_board_state_manager: RelayBoardStateManager = Depends(get_relay_board_state_manager),
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
        if not await relay_manager.set_channel_state(ch, 1):
            results.append({"channel": ch, "ok": False})
            continue
        await asyncio.sleep(duration_s)
        on_snapshot = relay_board_state_manager.get_snapshot()
        ok_on = on_snapshot.channels is not None and on_snapshot.channels[ch] is True

        # Set OFF, short wait, read back
        if not await relay_manager.set_channel_state(ch, 0):
            results.append({"channel": ch, "ok": False})
            continue
        await asyncio.sleep(0.05)
        off_snapshot = relay_board_state_manager.get_snapshot()
        ok_off = off_snapshot.channels is not None and off_snapshot.channels[ch] is False

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

    return {
        "channel": channel,
        "state": body.state,
        "ok": True,
    }


@router.get("/api/hardware/relays/state")
async def relay_state(
    relay_board_state_manager: RelayBoardStateManager = Depends(get_relay_board_state_manager),
    automation_redis: AutomationRedisClient = Depends(get_automation_redis),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
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

    snapshot = relay_board_state_manager.get_snapshot()
    return {
        "channels": list(snapshot.channels) if snapshot.channels is not None else None,
        "sampled_at": _timestamp(snapshot.sampled_at),
        "changed_at": [_timestamp(value) for value in snapshot.changed_at],
        "control_metadata": {
            "modes": modes,
            "override_expires_at": override_expires_at,
        },
    }


def _timestamp(value: datetime | None) -> str | None:
    """Serialize an observed timestamp in the API's ISO-8601 UTC format."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
