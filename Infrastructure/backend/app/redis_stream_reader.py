"""Redis Stream reader utility for querying sensor data from Redis Stream.

Supports reading from unified ``sensor:raw`` stream with filtering by type
(``can``/``soil``) and time range.

Async-only since Phase 2.4: every method that performs I/O is a coroutine.
The previous synchronous ``redis.Redis`` client blocked the FastAPI event
loop during every historical-query request path, which in turn stalled the
WebSocket broadcast loop (same event loop). ``redis.asyncio.Redis`` removes
that coupling.

Connection strategy: one connection per ``RedisStreamReader`` instance, opened
via ``connect()`` and released via ``close()``. Callers create a fresh reader
per request for now; migrating to a shared ``redis.asyncio`` pool is a Phase 4
follow-up — it buys throughput but doesn't affect correctness, so deferred.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
import json
import os
from typing import Any

import redis.asyncio as redis

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class RedisStreamReader:
    """Reads sensor data from Redis Stream by time range and type.

    All I/O methods are async coroutines. Callers must ``await`` them.
    """

    def __init__(self, redis_url: str | None = None, stream_name: str = "sensor:raw"):
        """Initialize Redis Stream reader.

        Args:
            redis_url: Redis connection URL (default: from env or localhost)
            stream_name: Name of the Redis Stream (default: 'sensor:raw')
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.stream_name = stream_name
        self.client: redis.Redis | None = None

    async def connect(self) -> bool:
        """Open a Redis connection. Returns True on success."""
        try:
            self.client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=False,  # keep binary for stream reads
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            await self.client.ping()
            logger.info(f"Connected to Redis Stream: {self.stream_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            return False

    async def get_stream_length(self) -> int:
        """Current entry count of the stream, or 0 on any error."""
        if not self.client and not await self.connect():
            return 0

        try:
            assert self.client is not None
            result = await self.client.xlen(self.stream_name)
            return result if isinstance(result, int) else 0
        except Exception as e:
            logger.warning(f"Error getting stream length: {e}")
            return 0

    async def read_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        sensor_type: str | None = None,
        max_count: int = 20000,
    ) -> list[dict[str, Any]]:
        """Read stream entries within a time range.

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            sensor_type: Filter by type ('can' or 'soil'), None for all
            max_count: Maximum number of entries to read (default: 20000)

        Returns:
            List of decoded stream entries matching the criteria
        """
        if not self.client and not await self.connect():
            return []

        try:
            start_ms = int(start_time.timestamp() * 1000)
            end_ms = int(end_time.timestamp() * 1000)

            # Use stream IDs to scope the XREVRANGE scan — avoids full-stream
            # scans for recent windows.
            max_id = f"{end_ms}-9999"
            min_id = f"{start_ms}-0"
            assert self.client is not None
            raw_entries = await self.client.xrevrange(
                self.stream_name, max=max_id, min=min_id, count=max_count
            )

            if not isinstance(raw_entries, list):
                return []

            entries: list[tuple[bytes, dict[bytes, bytes]]] = raw_entries

            if not entries:
                return []

            results = []
            for entry_id, fields in entries:
                ts_bytes = fields.get(b"ts")
                if not ts_bytes:
                    continue

                try:
                    entry_ts_ms = int(ts_bytes.decode("utf-8"))
                except (ValueError, AttributeError):
                    continue

                if entry_ts_ms < start_ms or entry_ts_ms > end_ms:
                    continue

                if sensor_type:
                    entry_type = fields.get(b"type")
                    if not entry_type:
                        continue
                    try:
                        entry_type_str = entry_type.decode("utf-8")
                        if entry_type_str != sensor_type:
                            continue
                    except (AttributeError, UnicodeDecodeError):
                        continue

                decoded_entry = self._decode_stream_entry(entry_id, fields)
                if decoded_entry:
                    results.append(decoded_entry)

            results.sort(key=lambda x: x.get("timestamp_ms", 0))

            return results

        except Exception as e:
            logger.error(f"Error reading from Redis Stream: {e}")
            return []

    def _decode_stream_entry(
        self, entry_id: bytes, fields: dict[bytes, bytes]
    ) -> dict[str, Any] | None:
        """Decode a stream entry to a dictionary. Pure-CPU, intentionally sync."""
        try:
            entry_id_str = (
                entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)
            )

            ts_bytes = fields.get(b"ts")
            ts_ms = None
            if ts_bytes:
                with contextlib.suppress(ValueError, AttributeError):
                    ts_ms = int(ts_bytes.decode("utf-8"))

            type_bytes = fields.get(b"type")
            entry_type = None
            if type_bytes:
                with contextlib.suppress(AttributeError, UnicodeDecodeError):
                    entry_type = type_bytes.decode("utf-8")

            decoded_data = None
            decoded_bytes = fields.get(b"decoded")
            if decoded_bytes:
                try:
                    decoded_str = decoded_bytes.decode("utf-8")
                    decoded_data = json.loads(decoded_str)
                except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                    pass

            raw_data = None
            data_bytes = fields.get(b"data")
            if data_bytes:
                with contextlib.suppress(AttributeError, UnicodeDecodeError):
                    raw_data = data_bytes.decode("utf-8")

            return {
                "id": entry_id_str,
                "timestamp_ms": ts_ms,
                "type": entry_type,
                "raw_data": raw_data,
                "decoded": decoded_data,
            }

        except Exception as e:
            logger.debug(f"Error decoding stream entry: {e}")
            return None

    async def close(self) -> None:
        """Release the Redis connection. Safe to call more than once."""
        if self.client:
            try:
                await self.client.aclose()
            except Exception as e:
                # Shutdown-only path; the process is going away. Debug-log so
                # we have a breadcrumb if a hang ever shows up here.
                logger.debug(f"RedisStreamReader.close: aclose() raised {type(e).__name__}: {e}")
            self.client = None

    async def __aenter__(self) -> RedisStreamReader:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
