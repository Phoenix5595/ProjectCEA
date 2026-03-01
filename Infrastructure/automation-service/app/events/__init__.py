"""ConfigEventBus - Real-time configuration change propagation.

This module provides an event-driven architecture for configuration changes:
- ConfigEventBus: Singleton event bus with bounded async queue
- ConfigChangeEvent: Dataclass for config change events
- ConfigEventType: Enum for supported event types

Usage:
    from app.events import ConfigEventBus, ConfigChangeEvent, ConfigEventType

    # Publish a config change
    bus = get_event_bus()
    event = ConfigChangeEvent(
        event_type=ConfigEventType.RAMP_TIMES_CHANGED,
        location="Flower Room",
        cluster="main",
        config_type="ramp_times",
        data={"ramp_up_minutes": 30}
    )
    await bus.publish(event)

    # Subscribe to events (async iterator)
    async for event in bus.subscribe():
        handle_config_change(event)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os

from app.events.redis_streams import RedisStreamPublisher
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ConfigEventType(Enum):
    """Types of configuration change events."""

    RAMP_TIMES_CHANGED = "ramp_times_changed"
    SETPOINT_CHANGED = "setpoint_changed"
    PID_PARAMS_CHANGED = "pid_params_changed"
    SCHEDULE_CHANGED = "schedule_changed"
    MODE_CHANGED = "mode_changed"


@dataclass
class ConfigChangeEvent:
    """Event payload for configuration changes.

    Attributes:
        event_type: Type of configuration change
        location: Room/location identifier (e.g., "Flower Room")
        cluster: Cluster identifier (e.g., "main")
        config_type: Configuration category (e.g., "ramp_times", "setpoints")
        timestamp: When the event occurred
        data: Configuration data payload
    """

    event_type: ConfigEventType
    location: str
    cluster: str
    config_type: str
    timestamp: datetime = field(default_factory=datetime.now)
    data: dict[str, object] = field(default_factory=dict)


class ConfigEventBus:
    """Singleton event bus for configuration change propagation.

    Thread-safe event bus with bounded queue to prevent memory exhaustion.
    Uses asyncio.Queue with maxsize for backpressure.

    Attributes:
        max_queue_size: Maximum events in queue (default: 100)
    """

    _instance: ConfigEventBus | None = None
    _queue: asyncio.Queue[ConfigChangeEvent]
    _subscribers: int
    max_queue_size: int = 100
    _redis_publisher: RedisStreamPublisher | None = None

    def __init__(self) -> None:
        """Initialize event bus with bounded queue."""
        # Only initialize once for singleton
        if not hasattr(self, "_queue"):
            self._queue = asyncio.Queue(maxsize=self.max_queue_size)
            self._subscribers = 0
        # Initialize Redis stream publisher for config events lazily on first use
        if self._redis_publisher is None:
            try:
                redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
                self._redis_publisher = RedisStreamPublisher(redis_url)
                logger.info("ConfigEventBus RedisStreamPublisher initialized (url=%s)", redis_url)
            except Exception as e:  # pragma: no cover
                logger.warning("ConfigEventBus failed to initialize RedisStreamPublisher: %s", e)
                self._redis_publisher = None

    def __new__(cls) -> ConfigEventBus:
        """Create or return singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def publish(self, event: ConfigChangeEvent) -> bool:
        """Publish event to queue. Non-blocking.

        Args:
            event: Configuration change event to publish

        Returns:
            True if event was queued, False if queue is full
        """
        try:
            self._queue.put_nowait(event)
            logger.debug(
                f"Published config event: {event.event_type.value} for {event.location}/{event.cluster}"
            )
            # Attempt to publish to Redis Stream in the background. Do not block the in-memory path.
            try:
                if self._redis_publisher is not None:
                    event_data = {
                        "event_type": event.event_type.value,
                        "location": event.location,
                        "cluster": event.cluster,
                        "config_type": event.config_type,
                        "timestamp": event.timestamp.isoformat(),
                        "data": event.data,
                    }
                    # Fire-and-forget publish to Redis Stream
                    asyncio.create_task(self._redis_publisher.publish(event_data))
                else:
                    # Redis publisher not available; rely on in-memory queue only
                    logger.debug(
                        "RedisStreamPublisher not configured; skipping Redis publish for event"
                    )
            except Exception as e:  # pragma: no cover
                # Do not break the in-memory path on Redis publish failure
                logger.warning("ConfigEventBus Redis publish dispatched failed: %s", e)
            return True
        except asyncio.QueueFull:
            logger.warning(
                f"ConfigEventBus queue full (max={self.max_queue_size}), dropping event: {event.event_type.value}"
            )
            return False

    async def get_event(self) -> ConfigChangeEvent:
        """Get next event from queue. Blocking async.

        Returns:
            Next configuration change event from queue
        """
        return await self._queue.get()

    async def subscribe(self) -> AsyncIterator[ConfigChangeEvent]:
        """Subscribe to config change events as async iterator.

        Yields:
            ConfigChangeEvent for each configuration change

        Example:
            async for event in bus.subscribe():
                if event.event_type == ConfigEventType.RAMP_TIMES_CHANGED:
                    await handle_ramp_change(event)
        """
        self._subscribers += 1
        try:
            while True:
                event = await self._queue.get()
                yield event
                self._queue.task_done()
        finally:
            self._subscribers -= 1

    @property
    def queue_size(self) -> int:
        """Current number of events in queue."""
        return self._queue.qsize()

    @property
    def subscriber_count(self) -> int:
        """Current number of active subscribers."""
        return self._subscribers

    def clear(self) -> None:
        """Clear all pending events from queue.

        Use with caution - primarily for testing.
        """
        while not self._queue.empty():
            try:
                _ = self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break


def get_event_bus() -> ConfigEventBus:
    """Get the singleton ConfigEventBus instance.

    Returns:
        The global ConfigEventBus instance
    """
    return ConfigEventBus()


__all__ = [
    "ConfigEventBus",
    "ConfigChangeEvent",
    "ConfigEventType",
    "get_event_bus",
]
