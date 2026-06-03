import asyncio
from datetime import datetime
import json
import os
from typing import Any

try:
    # Redis asyncio client (redis-py >= 4.x)
    import redis.asyncio as aioredis  # type: ignore
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore

# Import from the same package using a relative import to avoid package import issues
from shared.infra_logging import get_logger

from . import (
    ConfigChangeEvent,
    ConfigEventBus,
    ConfigEventType,
    get_event_bus,
)

logger = get_logger(__name__)


class RedisEventConsumer:
    """Redis Streams event consumer for configuration events.

    Subscribes to the stream 'cea:events:config' using consumer group
    'cea:events:group'. Events are parsed and published to the in-memory
    ConfigEventBus for local handlers. Messages are acknowledged after
    successful processing to avoid re-delivery.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        stream: str = "cea:events:config",
        group: str = "cea:events:group",
        consumer_name: str | None = None,
    ) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self.stream = stream
        self.group = group
        self.consumer_name = consumer_name or f"{os.uname().nodename}-automation"
        self._redis: aioredis.Redis | None = None  # type: ignore
        self._initialized = False

    async def _ensure_redis(self) -> None:
        if self._redis is not None:
            return
        if aioredis is None:
            logger.error("Redis asyncio client not available. Install 'redis' package.")
            return
        self._redis = aioredis.Redis.from_url(self.redis_url)
        try:
            if self._redis is not None:
                await self._redis.ping()
        except (ConnectionError, OSError) as e:  # pragma: no cover
            logger.warning("Redis ping failed in consumer: %s", e)
            # Non-fatal: consumer can still operate; errors surfaced when reading
        self._initialized = True

    async def _ensure_consumer_group(self) -> None:
        if self._redis is None:
            return
        try:
            # id '0' to start from beginning if stream is new
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as e:  # pragma: no cover
            msg = str(e).lower()
            if "busygrou" in msg or "exists" in msg or "group name already" in msg:
                logger.info(
                    "Consumer group '%s' already exists for stream '%s'.", self.group, self.stream
                )
                return
            logger.warning(
                "Failed to create consumer group '%s' for stream '%s': %s",
                self.group,
                self.stream,
                e,
            )

    async def start(self) -> None:
        """Start the Redis event consumption loop in the current task."""
        await self._ensure_redis()
        if self._redis is None:
            return
        await self._ensure_consumer_group()

        bus: ConfigEventBus = get_event_bus()
        # Main loop: read with timeout to avoid blocking indefinitely
        while True:
            try:
                # XREADGROUP GROUP <group> <consumer> STREAMS <stream> >
                # Read up to 20 new messages at a time, with a 1s block timeout
                result = await self._redis.xreadgroup(
                    self.group,
                    self.consumer_name,
                    {self.stream: ">"},
                    count=20,
                    block=1000,
                )
            except Exception as e:  # pragma: no cover
                logger.warning("Error reading from Redis stream '%s': %s", self.stream, e)
                await asyncio.sleep(0.5)
                continue

            if not result:
                # No new messages; loop and wait for next batch
                continue

            # result is a list of (stream, [(id, {field: value, ...}), ...])
            for _stream_name, messages in result:
                for msg_id, fields in messages:
                    try:
                        payload: dict[str, Any] = {
                            (k.decode() if isinstance(k, (bytes, bytearray)) else k): (
                                v.decode() if isinstance(v, (bytes, bytearray)) else v
                            )
                            for k, v in fields.items()
                        }

                        event_type_str = payload.get("event_type")
                        location = payload.get("location", "unknown").strip()
                        cluster = payload.get("cluster", "unknown").strip()
                        config_type = payload.get("config_type", "unknown")
                        ts_raw = payload.get("timestamp")

                        # Parse timestamp if available
                        event_ts = None
                        if isinstance(ts_raw, str):
                            try:
                                event_ts = datetime.fromisoformat(ts_raw)
                            except (ValueError, TypeError):
                                event_ts = datetime.now()
                        else:
                            event_ts = datetime.now()

                        # Try to parse data payload
                        data_raw = payload.get("data")
                        data: dict[str, object] = {}
                        if data_raw is not None:
                            # If it's a JSON string, try to load
                            if isinstance(data_raw, bytes):
                                data_str = data_raw.decode()
                            else:
                                data_str = str(data_raw)
                            try:
                                data = json.loads(data_str)  # type: ignore
                            except (json.JSONDecodeError, ValueError, TypeError):
                                # Fallback: store raw string under a key
                                data = {"raw": data_str}

                        # Build ConfigChangeEvent for in-memory bus
                        event_type = (
                            ConfigEventType(event_type_str)  # type: ignore[arg-type]
                            if event_type_str is not None
                            else ConfigEventType.RAMP_TIMES_CHANGED
                        )
                        event = ConfigChangeEvent(
                            event_type=event_type,
                            location=location,
                            cluster=cluster,
                            config_type=str(config_type),
                            timestamp=event_ts,
                            data=data,
                        )

                        # Publish to in-memory bus for local handlers
                        try:
                            await bus.publish(event)
                        except Exception as e:  # pragma: no cover
                            logger.warning("Failed to publish event to in-memory bus: %s", e)

                        # Acknowledge message to Redis so it won't be delivered again
                        try:
                            await self._redis.xack(self.stream, self.group, msg_id)
                        except Exception as e:  # pragma: no cover
                            logger.warning("Failed to ack Redis message %s: %s", msg_id, e)
                    except Exception as e:  # pragma: no cover
                        # Ensure one bad message doesn't break the whole loop
                        logger.exception("Error processing Redis stream message: %s", e)
                        # Best effort: nack by acknowledging so we don't loop on it.
                        # If the inner ack also fails we already logged the
                        # outer error above; debug-log this one so operators
                        # can see the double-failure but we don't promote it
                        # to warning (the loop will keep going either way).
                        try:
                            await self._redis.xack(self.stream, self.group, msg_id)
                        except Exception as e:
                            logger.debug("Inner xack(%s) failed during error path: %s", msg_id, e)
