"""WebSocket endpoints for real-time updates."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Store active WebSocket connections
active_connections: list = []


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import get_database as _get_database

    return _get_database()


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    from app.main import get_relay_manager as _get_relay_manager

    return _get_relay_manager()


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
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(f"WebSocket client connected. Total connections: {len(active_connections)}")

    try:
        # Send initial state
        database = get_database()
        relay_manager = get_relay_manager()
        automation_redis = get_automation_redis()

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
        logger.info(f"WebSocket client disconnected. Total connections: {len(active_connections)}")


# Function to broadcast sensor updates (called from background tasks)
async def broadcast_sensor_update(
    location: str, cluster: str, sensor_name: str, value: float, timestamp: float
) -> None:
    """Broadcast sensor update to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        sensor_name: Sensor name
        value: Sensor value
        timestamp: Timestamp (Unix timestamp)
    """
    await broadcast_message(
        {
            "type": "sensor_update",
            "location": location,
            "cluster": cluster,
            "sensor": sensor_name,
            "value": value,
            "timestamp": timestamp,
        }
    )


# Function to broadcast device state changes (called from control engine)
async def broadcast_device_update(
    location: str, cluster: str, device_name: str, state: int, mode: str
) -> None:
    """Broadcast device state change to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        device_name: Device name
        state: Device state (0/1)
        mode: Device mode
    """
    await broadcast_message(
        {
            "type": "device_update",
            "location": location,
            "cluster": cluster,
            "device": device_name,
            "state": state,
            "mode": mode,
        }
    )


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
    await broadcast_message(
        {
            "type": "schedule_update",
            "schedule_id": schedule_id,
            "schedule": schedule_data,
            "updated_at": schedule_data.get("updated_at").isoformat()
            if schedule_data.get("updated_at")
            else None,
        }
    )


# Function to broadcast setpoint updates
async def broadcast_setpoint_update(
    location: str, cluster: str, mode: str | None, setpoint_data: dict[str, Any]
) -> None:
    """Broadcast setpoint update to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        mode: Mode (DAY/NIGHT/TRANSITION) or None for legacy
        setpoint_data: Complete setpoint data dictionary
    """
    await broadcast_message(
        {
            "type": "setpoint_update",
            "location": location,
            "cluster": cluster,
            "mode": mode,
            "setpoint": setpoint_data,
            "updated_at": setpoint_data.get("updated_at").isoformat()
            if setpoint_data.get("updated_at")
            else None,
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


# Function to broadcast climate schedule updates
async def broadcast_climate_schedule_update(
    location: str, cluster: str, schedule_data: dict[str, Any]
) -> None:
    """Broadcast climate schedule update to all WebSocket clients.

    Args:
        location: Location name
        cluster: Cluster name
        schedule_data: Climate schedule data dictionary
    """
    await broadcast_message(
        {
            "type": "climate_schedule_update",
            "location": location,
            "cluster": cluster,
            "schedule": schedule_data,
        }
    )
