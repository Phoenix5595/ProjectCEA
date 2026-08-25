import logging

from shared.redis_keys import CONFIG_EVENTS_MAXLEN, CONFIG_EVENTS_STREAM

try:
    # Redis asyncio client (redis-py >= 4.x)
    import redis.asyncio as aioredis  # type: ignore
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore

logger = logging.getLogger(__name__)


class RedisStreamPublisher:
    """
    Minimal Redis Streams publisher for cross-service config events.
    Publishes events to the 'cea:events:config' stream and ensures a
    consumer group 'cea:events:group' exists.
    All operations are asynchronous and errors are logged as warnings.
    """

    def __init__(
        self,
        redis_url: str,
        stream: str = CONFIG_EVENTS_STREAM,
        group: str = "cea:events:group",
    ):
        self.redis_url = redis_url
        self.stream = stream
        self.group = group
        self._redis = None  # type: ignore
        self._initialized = False

    async def _ensure_redis(self):
        if self._redis is not None:
            return
        if aioredis is None:
            logger.error("Redis asyncio client not available. Install 'redis' package.")
            return
        # Lazy initialize the Redis asyncio client
        self._redis = aioredis.Redis.from_url(self.redis_url)
        # Optional: test connection
        try:
            await self._redis.ping()
        except (ConnectionError, OSError) as e:  # pragma: no cover
            logger.warning("Redis ping failed in publisher: %s", e)
            # If ping fails, still keep the client; publish will surface errors gracefully
        self._initialized = True

    async def publish(self, event_data: dict[str, object]):
        """
        Publish event_data to the configured Redis stream.
        Converts values to strings as required by Redis streams.
        """
        await self._ensure_redis()
        if self._redis is None:
            logger.warning(
                "Redis client not initialized. Skipping publish for stream '%s'.", self.stream
            )
            return
        try:
            mapping = {str(k): str(v) for k, v in event_data.items()}
            # XADD <stream> * key value ... with bounded reconnect retention.
            await self._redis.xadd(
                self.stream,
                mapping,
                maxlen=CONFIG_EVENTS_MAXLEN,
                approximate=True,
            )
        except Exception as e:  # pragma: no cover
            # Do not raise to avoid blocking service startup; log and continue
            logger.warning("Failed to publish event to Redis stream '%s': %s", self.stream, e)

    async def create_consumer_group(self):
        """
        Create the consumer group for the stream if it does not exist.
        Uses MKSTREAM to create the stream if needed.
        """
        await self._ensure_redis()
        if self._redis is None:
            logger.warning(
                "Redis client not initialized. Skipping consumer group creation for '%s'.",
                self.stream,
            )
            return
        try:
            # id '0' to start at the beginning; mkstream=True to create stream if needed
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as e:
            msg = str(e).lower()
            # Common case: group already exists
            if "busygrou" in msg or "exists" in msg or "group name already exists" in msg:
                logger.info(
                    "Consumer group '%s' already exists for stream '%s'.", self.group, self.stream
                )
                return
            # Other errors are logged as warnings but not raised
            logger.warning(
                "Failed to create consumer group '%s' for stream '%s': %s",
                self.group,
                self.stream,
                e,
            )
