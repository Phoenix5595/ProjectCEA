"""WebSocket endpoints for real-time updates."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from shared.auth import WebSocketConnectionLimiter, check_websocket_auth
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Store active WebSocket connections
active_connections: list[Any] = []

# Phase 3.2 connection cap. No-op when CEA_API_KEY_REQUIRE=false.
_ws_limiter = WebSocketConnectionLimiter()


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import container

    return container.get_database()


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    from app.main import container

    return container.get_relay_manager()


def get_automation_redis() -> AutomationRedisClient | None:
    """Get automation Redis client."""
    database = get_database()
    return database._automation_redis if database else None


async def broadcast_message(message: dict[str, Any]) -> None:
    """Broadcast a message to all connected WebSocket clients.

    Args:
        message: Message dict to broadcast
    """
    if not active_connections:
        return

    message_json = json.dumps(message)
    disconnected = []

    for connection in active_connections:
        try:
            await connection.send_text(message_json)
        except Exception as e:
            logger.warning(f"Error sending WebSocket message: {e}")
            disconnected.append(connection)

    # Remove disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time updates.

    Broadcasts:
    - Sensor data updates (temperature, humidity, CO2, VPD, etc.)
    - Device state changes
    - Mode changes
    - Alarm updates
    """
    # Phase 3.2: auth (token or origin) + connection cap. No-op when
    # CEA_API_KEY_REQUIRE=false.
    if not await check_websocket_auth(websocket):
        return
    if not await _ws_limiter.acquire(websocket):
        return
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total connections: {len(active_connections)}")

    try:
        # Send initial state
        database = get_database()
        relay_manager = get_relay_manager()
        get_automation_redis()

        if database and relay_manager:
            # Get all devices
            devices = []
            device_states = relay_manager.get_all_states()
            for (location, cluster, device_name), state in device_states.items():
                mode = relay_manager.get_device_mode(location, cluster, device_name) or "auto"
                devices.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "device_name": device_name,
                        "state": state,
                        "mode": mode,
                    }
                )

            await websocket.send_json({"type": "initial_state", "devices": devices})

        # Keep connection alive and handle incoming messages
        while True:
            # Wait for messages from client (ping/pong or subscription requests)
            try:
                data = await websocket.receive_text()
                try:
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass  # Ignore invalid JSON
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error receiving WebSocket message: {e}")
                break

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in active_connections:
            active_connections.remove(websocket)
        _ws_limiter.release()
        logger.info(f"WebSocket client disconnected. Total connections: {len(active_connections)}")


# Function to broadcast mode changes
async def broadcast_mode_update(location: str, cluster: str, mode: str) -> None:
    """Broadcast mode change to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        mode: New mode
    """
    await broadcast_message(
        {"type": "mode_update", "location": location, "cluster": cluster, "mode": mode}
    )


# Function to broadcast schedule updates
async def broadcast_schedule_update(schedule_id: int, schedule_data: dict[str, Any]) -> None:
    """Broadcast schedule update to all WebSocket clients.

    Args:
        schedule_id: Schedule ID
        schedule_data: Complete schedule data dictionary
    """
    updated_at = schedule_data.get("updated_at")
    await broadcast_message(
        {
            "type": "schedule_update",
            "schedule_id": schedule_id,
            "schedule": schedule_data,
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        }
    )


# Function to broadcast room schedule updates
async def broadcast_room_schedule_update(
    location: str, cluster: str, schedule_data: dict[str, Any]
) -> None:
    """Broadcast room schedule update to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        schedule_data: Room schedule data dictionary
    """
    await broadcast_message(
        {
            "type": "room_schedule_update",
            "location": location,
            "cluster": cluster,
            "schedule": schedule_data,
        }
    )
