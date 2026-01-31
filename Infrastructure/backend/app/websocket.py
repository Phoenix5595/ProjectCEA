"""WebSocket manager for real-time data broadcasting."""

from __future__ import annotations

import json
from datetime import datetime

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


# Global WebSocket manager instance
websocket_manager = WebSocketManager()
