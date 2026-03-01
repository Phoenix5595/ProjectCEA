"""WebSocket manager for real-time data broadcasting."""

from __future__ import annotations

from datetime import datetime
import json

from fastapi import WebSocket

from app.models import WebSocketMessage


class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        """Initialize WebSocket manager."""
        # Map location -> set of WebSocket connections
        self.active_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, location: str):
        """Connect a WebSocket client for a specific location."""
        await websocket.accept()

        if location not in self.active_connections:
            self.active_connections[location] = set()

        self.active_connections[location].add(websocket)

    def disconnect(self, websocket: WebSocket, location: str):
        """Disconnect a WebSocket client."""
        if location in self.active_connections:
            self.active_connections[location].discard(websocket)

            # Clean up empty sets
            if not self.active_connections[location]:
                del self.active_connections[location]

    async def broadcast_sensor_update(
        self,
        location: str,
        cluster: str,
        sensor_type: str,
        timestamp: datetime,
        value: float,
        unit: str,
    ):
        """Broadcast sensor update to all connected clients for a location."""
        if location not in self.active_connections:
            return

        message = WebSocketMessage(
            type="sensor_update",
            location=location,
            cluster=cluster,
            sensor_type=sensor_type,
            timestamp=timestamp,
            value=value,
            unit=unit,
        )
        # Include "sensor" for frontend (Dashboard uses message.sensor for key)
        payload = message.model_dump(mode="json")
        payload["sensor"] = sensor_type
        message_json = json.dumps(payload)
        disconnected = set()

        for connection in self.active_connections[location]:
            try:
                await connection.send_text(message_json)
            except Exception:
                disconnected.add(connection)

        # Remove disconnected connections
        for conn in disconnected:
            self.disconnect(conn, location)

    async def broadcast_config_event(
        self,
        location: str,
        payload: dict[str, object],
    ) -> None:
        """Broadcast a config change event to WebSocket clients.

        Sends to clients subscribed to the given *location*.  If the location
        is ``"unknown"`` or ``"all"``, the event is broadcast to **every**
        connected client.
        """
        message_json = json.dumps(payload, default=str)

        if location in ("unknown", "all"):
            target_locations = list(self.active_connections.keys())
        else:
            target_locations = [location] if location in self.active_connections else []

        for loc in target_locations:
            disconnected: set[WebSocket] = set()
            for connection in self.active_connections.get(loc, set()):
                try:
                    await connection.send_text(message_json)
                except Exception:
                    disconnected.add(connection)

            for conn in disconnected:
                self.disconnect(conn, loc)


# Global WebSocket manager instance
websocket_manager = WebSocketManager()
