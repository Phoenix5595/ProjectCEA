"""Hardware endpoints: MCP23017 relay test and state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.control.device_command_service import DeviceCommandService
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
from app.redis.schema import relay_raw_override_key
from app.redis_client import AutomationRedisClient

router = APIRouter()


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_relay_board_state_manager() -> RelayBoardStateManager:
    """Dependency to get the in-process relay board snapshot owner."""
    raise RuntimeError("Dependency not injected")


def get_automation_redis() -> AutomationRedisClient:
    """Dependency to get AutomationRedisClient."""
    raise RuntimeError("Dependency not injected")


def get_device_command_service() -> DeviceCommandService:
    """Dependency to get the assigned-device command authority."""
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
@emits_operational_mutation
async def set_relay_channel_state(
    channel: int,
    body: RelayChannelControlRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    automation_redis: AutomationRedisClient = Depends(get_automation_redis),
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
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
    redis_client = automation_redis.redis_client
    previous_override = (
        await asyncio.to_thread(redis_client.get, override_key)
        if redis_client is not None
        else None
    )
    before = _raw_override_values(previous_override)

    if body.state == 1:
        if body.duration_seconds is None:
            raise HTTPException(status_code=400, detail="Raw ON requires a duration_seconds value")
        if device_command_service.is_assigned_channel(channel):
            raise HTTPException(
                status_code=409,
                detail="Raw ON is forbidden for an assigned relay channel",
            )
        expires_at = datetime.now(UTC) + timedelta(seconds=body.duration_seconds)
        success = await relay_manager.set_channel_state(channel, 1)
        if not success:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to set channel {channel} to ON",
            )
        if redis_client is not None:
            persisted_override = {"expires_at": expires_at.isoformat(), "state": 1}
            await asyncio.to_thread(
                redis_client.setex,
                override_key,
                body.duration_seconds + 86400,
                json.dumps(persisted_override),
            )
    else:
        persisted_override = {}
        if redis_client is not None:
            await asyncio.to_thread(
                redis_client.delete,
                override_key,
            )
        success = await relay_manager.set_channel_state(channel, 0)
        if not success:
            raise HTTPException(
                status_code=503,
                detail=f"Failed to set channel {channel} to OFF",
            )

    if redis_client is not None and before is not None:
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="raw_relay_override", entity_id=str(channel)),
                changes=safe_allowlisted_diff(
                    before=before,
                    after=persisted_override,
                    allowed_fields=frozenset({"expires_at", "state"}),
                ),
            ),
            context,
        )

    return {
        "channel": channel,
        "state": body.state,
        "ok": True,
    }


@router.get("/api/hardware/relays/state")
async def relay_state(
    relay_board_state_manager: RelayBoardStateManager = Depends(get_relay_board_state_manager),
) -> dict[str, Any]:
    snapshot = relay_board_state_manager.get_snapshot()
    freshness = relay_board_state_manager.get_freshness()
    return {
        "channels": list(snapshot.channels) if snapshot.channels is not None else None,
        "sampled_at": _timestamp(snapshot.sampled_at),
        "changed_at": [_timestamp(value) for value in snapshot.changed_at],
        "freshness": freshness.status,
        "stale_since": _timestamp(freshness.stale_since),
    }


def _timestamp(value: datetime | None) -> str | None:
    """Serialize an observed timestamp in the API's ISO-8601 UTC format."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _raw_override_values(value: bytes | str | None) -> dict[str, str | int] | None:
    if value is None:
        return {}
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    state = decoded.get("state")
    expires_at = decoded.get("expires_at")
    if type(state) is not int or state not in {0, 1} or not isinstance(expires_at, str):
        return None
    return {"expires_at": expires_at, "state": state}
