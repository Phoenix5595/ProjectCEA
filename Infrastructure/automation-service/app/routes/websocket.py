"""WebSocket endpoints for real-time updates."""
import json
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from app.database import DatabaseManager
from app.redis_client import AutomationRedisClient
from app.control.relay_manager import RelayManager

logger = logging.getLogger(__name__)

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


def get_automation_redis() -> Optional[AutomationRedisClient]:
    """Get automation Redis client."""
    database = get_database()
    return database._automation_redis if database else None


async def broadcast_message(message: Dict[str, Any]):
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
async def websocket_endpoint(websocket: WebSocket):
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
                mode = relay_manager.get_device_mode(location, cluster, device_name) or 'auto'
                devices.append({
                    "location": location,
                    "cluster": cluster,
                    "device_name": device_name,
                    "state": state,
                    "mode": mode
                })
            
            await websocket.send_json({
                "type": "initial_state",
                "devices": devices
            })
        
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
async def broadcast_sensor_update(location: str, cluster: str, sensor_name: str, value: float, timestamp: float):
    """Broadcast sensor update to all WebSocket clients.
    
    Args:
        location: Location name
        cluster: Cluster name
        sensor_name: Sensor name
        value: Sensor value
        timestamp: Timestamp (Unix timestamp)
    """
    await broadcast_message({
        "type": "sensor_update",
        "location": location,
        "cluster": cluster,
        "sensor": sensor_name,
        "value": value,
        "timestamp": timestamp
    })


# Function to broadcast device state changes (called from control engine)
async def broadcast_device_update(location: str, cluster: str, device_name: str, state: int, mode: str):
    """Broadcast device state change to all WebSocket clients.
    
    Args:
        location: Location name
        cluster: Cluster name
        device_name: Device name
        state: Device state (0/1)
        mode: Device mode
    """
    await broadcast_message({
        "type": "device_update",
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "state": state,
        "mode": mode
    })


# Function to broadcast mode changes
async def broadcast_mode_update(location: str, cluster: str, mode: str):
    """Broadcast mode change to all WebSocket clients.
    
    Args:
        location: Location name
        cluster: Cluster name
        mode: New mode
    """
    await broadcast_message({
        "type": "mode_update",
        "location": location,
        "cluster": cluster,
        "mode": mode
    })

