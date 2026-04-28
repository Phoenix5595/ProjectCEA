"""Redis Streams consumer for configuration change events.

Subscribes to ``cea:events:config`` and pushes received events to
connected WebSocket clients via the backend's WebSocketManager.

Falls back gracefully when Redis is unavailable – the backend still
serves sensor data, just without live config push notifications.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
from typing import Any

try:
    import redis.asyncio as aioredis

    _has_redis = True
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]
    _has_redis = False

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ConfigEventConsumer:
    """Consume config events from Redis Streams and push to WebSocket clients.

    Uses a dedicated consumer group (``cea:events:backend-group``) so that
    events are delivered independently of the automation-service consumer.

    Parameters
    ----------
    redis_url:
        Redis connection URL.  Defaults to ``REDIS_URL`` env var.
    stream:
        Redis Stream key to read from.
    group:
        Consumer group name.
    consumer_name:
        Unique consumer name within the group.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream: str = "cea:events:config",
        group: str = "cea:events:backend-group",
        consumer_name: str | None = None,
    ) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name or f"{os.uname().nodename}-backend"
        self._redis: Any = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    async def _ensure_redis(self) -> bool:
        """Create Redis connection if not yet established.

        Returns True when Redis is available, False otherwise.
        """
        if self._redis is not None:
            return True
        if not _has_redis:
            logger.warning("redis.asyncio not available – config event consumer disabled")
            return False
        try:
            if aioredis is None:
                return False
            self._redis = aioredis.Redis.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
            logger.info("Config event consumer connected to Redis at %s", self.redis_url)
            return True
        except Exception as exc:
            logger.warning("Config event consumer failed to connect to Redis: %s", exc)
            self._redis = None
            return False

    async def _ensure_consumer_group(self) -> None:
        """Create the consumer group if it does not already exist."""
        if self._redis is None:
            return
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
            logger.info("Created consumer group '%s' for stream '%s'", self.group, self.stream)
        except Exception as exc:
            msg = str(exc).lower()
            if "busygrou" in msg or "exists" in msg or "group name already" in msg:
                logger.debug(
                    "Consumer group '%s' already exists for stream '%s'",
                    self.group,
                    self.stream,
                )
            else:
                logger.warning("Failed to create consumer group '%s': %s", self.group, exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the consumer loop as a background ``asyncio.Task``."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("Config event consumer started")

    async def stop(self) -> None:
        """Cancel the background task and close Redis."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._redis is not None:
            try:
                await self._redis.aclose()  # type: ignore[union-attr]
            except Exception as e:
                logger.debug("Redis aclose() raised during stop(): %s", e)
            self._redis = None
        logger.info("Config event consumer stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Read from Redis Stream and broadcast to WebSocket clients."""
        # Lazy import to avoid circular dependency at module load
        from app.websocket import websocket_manager

        reconnect_delay = 2.0
        max_reconnect_delay = 30.0

        while self._running:
            # (Re)connect to Redis
            connected = await self._ensure_redis()
            if not connected:
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            reconnect_delay = 2.0  # reset on success
            await self._ensure_consumer_group()

            try:
                await self._read_loop(websocket_manager)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Config event consumer error, will reconnect: %s", exc)
                # Reset connection so next iteration reconnects
                self._redis = None
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _read_loop(self, ws_manager: Any) -> None:
        """Inner read loop – separated for clean reconnect handling."""
        assert self._redis is not None  # ensured by caller  # noqa: S101

        while self._running:
            try:
                result = await self._redis.xreadgroup(
                    self.group,
                    self.consumer_name,
                    {self.stream: ">"},
                    count=20,
                    block=1000,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Error reading from stream '%s': %s", self.stream, exc)
                await asyncio.sleep(0.5)
                continue

            if not result:
                continue

            for _stream_name, messages in result:
                for msg_id, fields in messages:
                    try:
                        await self._process_message(fields, ws_manager)
                    except Exception as exc:  # pragma: no cover
                        logger.exception(
                            "Error processing config event message %s: %s", msg_id, exc
                        )
                        await self._write_dlq(msg_id, fields, exc)

                    # Acknowledge regardless – avoid infinite re-delivery of bad msgs
                    try:
                        await self._redis.xack(self.stream, self.group, msg_id)
                    except Exception as exc:  # pragma: no cover
                        logger.warning("Failed to ACK config event message %s: %s", msg_id, exc)

    async def _write_dlq(
        self,
        msg_id: str,
        fields: dict[str, Any],
        exc: Exception,
    ) -> None:
        """Best-effort DLQ write before acknowledging a failed event."""
        if self._redis is None:
            return
        try:
            await self._redis.xadd(
                "sensor:dlq",
                {
                    "source_stream": self.stream,
                    "source_group": self.group,
                    "source_message_id": msg_id,
                    "error": str(exc),
                    "payload": json.dumps(fields, sort_keys=True, default=str),
                    "timestamp": datetime.now().isoformat(),
                },
                maxlen=1000,
                approximate=True,
            )
        except Exception as dlq_exc:  # pragma: no cover
            logger.warning("Failed to write config event %s to DLQ: %s", msg_id, dlq_exc)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def _process_message(self, fields: dict[str, Any], ws_manager: Any) -> None:
        """Parse a stream message and broadcast to WebSocket clients."""
        # Fields are already decoded (decode_responses=True)
        event_type = fields.get("event_type", "unknown")
        location = fields.get("location", "unknown")
        cluster = fields.get("cluster", "unknown")
        config_type = fields.get("config_type", "unknown")
        ts_raw = fields.get("timestamp")

        # Parse timestamp
        event_ts: str
        if isinstance(ts_raw, str):
            try:
                event_ts = datetime.fromisoformat(ts_raw).isoformat()
            except Exception:
                event_ts = datetime.now().isoformat()
        else:
            event_ts = datetime.now().isoformat()

        # Parse nested data payload
        data: dict[str, object] = {}
        data_raw = fields.get("data")
        if data_raw is not None:
            try:
                data = json.loads(str(data_raw))
            except Exception:
                data = {"raw": str(data_raw)}

        # Build WebSocket payload
        payload: dict[str, object] = {
            "type": "config_update",
            "event_type": event_type,
            "location": location,
            "cluster": cluster,
            "config_type": config_type,
            "timestamp": event_ts,
            "data": data,
        }

        await ws_manager.broadcast_config_event(location, payload)

        logger.debug("Broadcast config event %s for %s/%s", event_type, location, cluster)
