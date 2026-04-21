"""Writer for Redis Stream, TimescaleDB and Redis state keys."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import threading
from typing import Any, cast

import psycopg2
import psycopg2.extensions
import psycopg2.extras
import redis
import redis.exceptions

from shared.db_batch_writer import BatchQueue
from shared.db_credentials import load_postgres_password
from shared.infra_logging import get_logger
from shared.redis_client import close_sync, create_sync_client
from shared.redis_keys import SENSOR_RAW_MAXLEN

logger = get_logger(__name__)


@dataclass
class DBWriteItem:
    decoded: dict[str, Any]
    raw_data: str
    sensors: list[tuple[str, float, str]]
    timestamp: object  # datetime


class DataWriter:
    """Writes processed CAN data to Redis Stream, TimescaleDB and Redis state keys."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        redis_url: str | None = None,
        redis_ttl: int = 10,
        stream_name: str = "sensor:raw",
    ):
        """Initialize data writer.

        Args:
            db_config: TimescaleDB connection config
            redis_url: Redis connection URL
            redis_ttl: TTL for Redis keys in seconds
            stream_name: Redis Stream name (default: sensor:raw)
        """
        if db_config is None:
            password = load_postgres_password()
            self.db_config: dict[str, Any] = {
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "database": os.getenv("POSTGRES_DB", "cea_sensors"),
                "user": os.getenv("POSTGRES_USER", "cea_user"),
                "password": password,
            }
        else:
            self.db_config = db_config
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_ttl = redis_ttl
        self.stream_name = stream_name

        self.db_conn: psycopg2.extensions.connection | None = None
        self.redis_client: redis.Redis | None = None
        self.redis_state_client: redis.Redis | None = (
            None  # Separate client for state writes (decode_responses=True)
        )
        # Pools tracked as fields so ``close()`` can call ``pool.disconnect()``
        # (pre-lift this was leaked on shutdown — pools were GC'd eventually
        # but not explicitly disconnected during the SIGTERM window).
        self._redis_stream_pool: redis.ConnectionPool | None = None
        self._redis_state_pool: redis.ConnectionPool | None = None

        self.db_enabled = False
        self.redis_enabled = False

        # Cache for device and sensor lookups (avoid repeated queries)
        self.device_cache: dict[str, int] = {}  # {device_name: device_id}
        self.sensor_cache: dict[tuple[int, str], int] = {}  # {(device_id, sensor_name): sensor_id}

        self._db_write_lock = threading.Lock()
        self._batch_queue: BatchQueue = BatchQueue(
            flush_callback=self._flush_batch,
            max_queue=10_000,
            flush_threshold=50,
            flush_interval_sec=0.1,
            name="can-db-flush",
        )

    def connect_db(self) -> bool:
        """Connect to TimescaleDB with optimizations for high throughput."""
        try:
            # Add connection parameters for better performance
            db_config_optimized = self.db_config.copy()
            # Add TCP keepalive to detect stale connections faster
            db_config_optimized["keepalives"] = 1
            db_config_optimized["keepalives_idle"] = 30
            db_config_optimized["keepalives_interval"] = 10
            db_config_optimized["keepalives_count"] = 3
            db_config_optimized["connect_timeout"] = 5
            self.db_conn = psycopg2.connect(**db_config_optimized)
            if self.db_conn is None:
                raise Exception("Failed to create database connection")
            self.db_conn.autocommit = True
            cursor = self.db_conn.cursor()
            cursor.execute("SET statement_timeout = '5000'")
            cursor.close()
            self.db_enabled = True
            logger.info("Connected to TimescaleDB (async batching enabled)")
            self._batch_queue.start()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            self.db_enabled = False
            return False

    def _prefetch_device_ids(self, cursor: Any, device_names: set[str]) -> None:
        """Load missing device_id rows in one query per batch (avoids N+1 SELECTs)."""
        missing = [n for n in device_names if n not in self.device_cache]
        if not missing:
            return
        cursor.execute(
            "SELECT device_id, name FROM device WHERE name = ANY(%s::text[])",
            (missing,),
        )
        for device_id, name in cursor.fetchall():
            self.device_cache[str(name)] = int(device_id)

    def _prefetch_sensor_ids(self, cursor: Any, by_device: dict[int, set[str]]) -> None:
        """Load missing (device_id, sensor name) rows grouped per device (one query per device)."""
        for device_id, names in by_device.items():
            missing = [s for s in names if (device_id, s) not in self.sensor_cache]
            if not missing:
                continue
            cursor.execute(
                "SELECT sensor_id, name FROM sensor WHERE device_id = %s AND name = ANY(%s::text[])",
                (device_id, missing),
            )
            for sensor_id, name in cursor.fetchall():
                self.sensor_cache[(device_id, str(name))] = int(sensor_id)

    def _flush_batch(self, items):
        """Flush callback invoked by :class:`BatchQueue`.

        BatchQueue owns the queued/flushed/dropped counters: a clean return
        from this callback counts as ``flushed += len(items)``; raising
        counts as ``dropped += len(items)`` and BatchQueue logs the error.
        """
        if not items or not self.db_enabled:
            raise RuntimeError("db not enabled; dropping batch")
        with self._db_write_lock:
            if not self._check_db_connection():
                raise RuntimeError("db connection unavailable; dropping batch")
            if self.db_conn is None:
                raise RuntimeError("db connection is None; dropping batch")
            try:
                cursor = self.db_conn.cursor()
                device_names: set[str] = set()
                for item in items:
                    node_id = item.decoded.get("node_id")
                    if not node_id:
                        continue
                    device_names.add(f"Node {node_id}")
                self._prefetch_device_ids(cursor, device_names)

                by_device: dict[int, set[str]] = {}
                for item in items:
                    node_id = item.decoded.get("node_id")
                    if not node_id:
                        continue
                    device_name = f"Node {node_id}"
                    if device_name not in self.device_cache:
                        continue
                    device_id = self.device_cache[device_name]
                    for sensor_name, _value, _unit in item.sensors:
                        cache_key = (device_id, sensor_name)
                        if cache_key not in self.sensor_cache:
                            by_device.setdefault(device_id, set()).add(sensor_name)
                self._prefetch_sensor_ids(cursor, by_device)

                measurements = []
                for item in items:
                    node_id = item.decoded.get("node_id")
                    if not node_id:
                        continue
                    device_name = f"Node {node_id}"
                    if device_name not in self.device_cache:
                        continue
                    device_id = self.device_cache[device_name]
                    for sensor_name, value, _unit in item.sensors:
                        cache_key = (device_id, sensor_name)
                        if cache_key not in self.sensor_cache:
                            continue
                        measurements.append(
                            (item.timestamp, self.sensor_cache[cache_key], value, None)
                        )
                if measurements:
                    psycopg2.extras.execute_batch(
                        cursor,
                        "INSERT INTO measurement (time, sensor_id, value, status) VALUES (%s, %s, %s, %s) ON CONFLICT (time, sensor_id) DO UPDATE SET value = EXCLUDED.value",
                        measurements,
                        page_size=500,
                    )
                cursor.close()
            except Exception:
                # Re-raise so BatchQueue counts the batch as dropped +
                # emits the flush-callback error log.
                raise

    def _check_db_connection(self) -> bool:
        """Check if database connection is alive and reconnect if needed.

        Returns:
            True if connection is healthy or reconnection succeeded, False otherwise
        """
        if not self.db_conn:
            if not self.connect_db():
                return False

        # Check connection health before write (handles reconnection)
        try:
            # Use a lightweight query to check connection health
            if self.db_conn is None:
                return False
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning(f"Database connection lost, attempting reconnect: {e}")
            if self.db_conn:
                try:
                    self.db_conn.close()
                except Exception as e:
                    logger.debug(f"Error closing stale connection: {e}")
            self.db_conn = None
            self.db_enabled = False
            return self.connect_db()
        except Exception as e:
            logger.error(f"Unexpected error checking DB connection: {e}")
            return False

    def connect_redis(self) -> bool:
        """Connect to Redis with connection pooling for better performance."""
        try:
            self.redis_client, self._redis_stream_pool = create_sync_client(
                self.redis_url,
                decode_responses=False,
                max_connections=10,
                name="can-redis-stream",
            )
            self.redis_state_client, self._redis_state_pool = create_sync_client(
                self.redis_url,
                decode_responses=True,
                max_connections=10,
                name="can-redis-state",
            )
            self.redis_enabled = True
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}. Continuing without Redis.")
            self.redis_enabled = False
            return False

    def write_to_stream(self, msg, decoded_data: dict[str, Any]) -> bool:
        """Write CAN message to Redis Stream.

        Args:
            msg: CAN message object
            decoded_data: Decoded CAN frame data

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            if not self.connect_redis():
                return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)
            raw_data = " ".join(f"{b:02X}" for b in msg.data)

            # Create stream entry with type="can" marker
            stream_data = {
                b"id": f"0x{msg.arbitration_id:03X}".encode(),
                b"data": raw_data.encode(),
                b"ts": str(timestamp_ms).encode(),
                b"dlc": str(msg.dlc).encode(),
                b"type": b"can",  # Mark as CAN sensor data
            }

            # Add decoded data if available
            if decoded_data:
                decoded_json = json.dumps(decoded_data)
                stream_data[b"decoded"] = decoded_json.encode()

            if self.redis_client:
                self.redis_client.xadd(
                    self.stream_name,
                    cast(Any, stream_data),
                    maxlen=SENSOR_RAW_MAXLEN,
                    approximate=True,
                )
            return True
        except Exception as e:
            # Don't log error for every message, just occasionally
            if not hasattr(self, "_stream_error_count"):
                self._stream_error_count = 0
            self._stream_error_count += 1

            if self._stream_error_count <= 5:
                logger.warning(f"Error writing to Redis Stream: {e}")
            elif self._stream_error_count == 6:
                logger.warning("Redis Stream errors continuing (suppressing further messages)...")

            return False

    def queue_db_write(self, decoded, raw_data, sensors, timestamp):
        # When DB was lost, try reconnect once so recovery does not require service restart
        if not self.db_enabled:
            if not self.connect_db():
                return False
        item = DBWriteItem(decoded=decoded, raw_data=raw_data, sensors=sensors, timestamp=timestamp)
        ok = self._batch_queue.put(item)
        if not ok:
            logger.error("DB write queue full, dropping measurement")
            return False
        # Surface sustained backpressure at ~80% queue depth. BatchQueue
        # exposes ``in_queue`` via its stats() snapshot.
        in_queue = self._batch_queue.stats()["in_queue"]
        if in_queue > 8000 and in_queue % 1000 == 0:
            logger.warning(f"DB write queue depth high: {in_queue}/10000")
        return True

    def get_stats(self):
        stats = self._batch_queue.stats()
        return {
            "queued": stats["queued"],
            "flushed": stats["flushed"],
            "dropped": stats["dropped"],
            "pending": stats["in_queue"],
        }

    def write_to_db(
        self,
        decoded: dict[str, Any],
        raw_data: str,
        sensors: list[tuple[str, float, str]],
        timestamp: datetime,
    ) -> bool:
        """Write decoded data to TimescaleDB measurement table.

        Args:
            decoded: Decoded CAN frame data
            raw_data: Raw hex data string
            sensors: List of (sensor_name, value, unit) tuples
            timestamp: Timestamp for the data

        Returns:
            True if successful, False otherwise
        """
        if not self.db_enabled:
            if not self.connect_db():
                return False

        # Check connection health before write (handles reconnection)
        if not self._check_db_connection():
            return False

        if not sensors:
            return True

        conn = self.db_conn
        if conn is None:
            return False

        try:
            cursor = conn.cursor()
            node_id = decoded.get("node_id")

            if not node_id:
                logger.warning("Missing node_id, skipping measurement write")
                return False

            # Get device_id from node_id (use cache to avoid repeated queries)
            device_name = f"Node {node_id}"
            if device_name not in self.device_cache:
                cursor.execute(
                    """
                    SELECT device_id FROM device WHERE name = %s
                """,
                    (device_name,),
                )
                device_row = cursor.fetchone()
                if not device_row:
                    logger.warning(f"Device not found: {device_name}, skipping measurement write")
                    return False
                self.device_cache[device_name] = device_row[0]
            device_id = self.device_cache[device_name]

            # Insert measurements for each sensor (use cache to avoid repeated queries)
            measurements = []
            for sensor_name, value, _unit in sensors:
                cache_key = (device_id, sensor_name)
                if cache_key not in self.sensor_cache:
                    cursor.execute(
                        """
                        SELECT sensor_id FROM sensor
                        WHERE device_id = %s AND name = %s
                    """,
                        (device_id, sensor_name),
                    )
                    sensor_row = cursor.fetchone()
                    if not sensor_row:
                        logger.debug(
                            f"Sensor not found: {sensor_name} (device: {device_id}), skipping"
                        )
                        continue
                    self.sensor_cache[cache_key] = sensor_row[0]
                sensor_id = self.sensor_cache[cache_key]

                measurements.append((timestamp, sensor_id, value, None))  # status is None for now

            if not measurements:
                return True

            # Batch insert measurements (autocommit handles commit automatically)
            # Use larger page_size for better performance
            psycopg2.extras.execute_batch(
                cursor,
                """
                INSERT INTO measurement (time, sensor_id, value, status)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (time, sensor_id) DO UPDATE
                SET value = EXCLUDED.value, status = EXCLUDED.status
                """,
                measurements,
                page_size=500,  # Larger batch for faster inserts
            )

            # No need to commit - autocommit is enabled
            return True

        except psycopg2.OperationalError as e:
            logger.error(f"TimescaleDB connection error: {e}")
            self.db_conn = None
            self.db_enabled = False
            return False
        except Exception as e:
            logger.error(f"Error writing to TimescaleDB: {e}")
            # With autocommit, no rollback needed
            return False

    def write_to_redis_state(
        self, sensors: list[tuple[str, float, str]], timestamp_ms: int
    ) -> bool:
        """Write sensor values to Redis state keys.

        Uses the pre-created pooled Redis client for better performance.
        This is a CRITICAL performance fix - previously created a new client on every call.

        Args:
            sensors: List of (sensor_name, value, unit) tuples
            timestamp_ms: Timestamp in milliseconds

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            if not self.connect_redis():
                return False

        if not sensors:
            return True

        try:
            # Use the pre-created pooled state client (no longer creates new client per call)
            if not self.redis_state_client:
                logger.warning("Redis state client not initialized, attempting reconnection")
                if not self.connect_redis():
                    return False

            if not self.redis_state_client:
                return False

            # Use pipeline for batch operations
            pipe = self.redis_state_client.pipeline()

            for sensor_name, value, _unit in sensors:
                # Set sensor value with NO TTL (persistent) - values should always be in Redis
                # This prevents database fallback and reduces CPU usage
                key = f"sensor:{sensor_name}"
                pipe.set(key, str(value))  # No TTL - persistent key

                # Set timestamp (also persistent)
                ts_key = f"sensor:{sensor_name}:ts"
                pipe.set(ts_key, str(timestamp_ms))  # No TTL - persistent key

            # Execute all commands
            pipe.execute()
            return True

        except redis.exceptions.ConnectionError as e:
            logger.warning(f"Redis connection error: {e}")
            self.redis_enabled = False
            return False
        except Exception as e:
            logger.warning(f"Error writing to Redis state: {e}")
            return False

    def write(
        self,
        msg,
        decoded: dict[str, Any],
        raw_data: str,
        sensors: list[tuple[str, float, str]],
        timestamp: datetime,
        timestamp_ms: int,
    ) -> dict[str, bool]:
        """Write data to Redis Stream, TimescaleDB and Redis state immediately.

        Args:
            msg: CAN message object
            decoded: Decoded CAN frame data
            raw_data: Raw hex data string
            sensors: List of sensor values to write to Redis and DB
            timestamp: Timestamp for database
            timestamp_ms: Timestamp in milliseconds for Redis

        Returns:
            Dictionary with 'stream', 'db' and 'redis' keys indicating success
        """
        result = {"stream": False, "db": False, "redis": False}

        # Write to Redis Stream first
        result["stream"] = self.write_to_stream(msg, decoded)

        result["db"] = self.queue_db_write(decoded, raw_data, sensors, timestamp)

        # Write to Redis state immediately
        result["redis"] = self.write_to_redis_state(sensors, timestamp_ms)

        return result

    def close(self):
        """Close all connections and stop flush thread."""
        # BatchQueue.stop() drains remaining items and invokes the flush
        # callback one last time before joining the worker thread.
        self._batch_queue.stop(drain_timeout_sec=2.0)
        stats = self.get_stats()
        logger.info(
            f"DB batch stats: queued={stats['queued']}, flushed={stats['flushed']}, dropped={stats['dropped']}"
        )
        if self.db_conn:
            try:
                self.db_conn.close()
            except Exception as e:
                logger.debug(f"Error closing DB connection: {e}")
            self.db_conn = None

        close_sync(self.redis_client, self._redis_stream_pool, name="can-redis-stream")
        close_sync(self.redis_state_client, self._redis_state_pool, name="can-redis-state")
        self.redis_client = None
        self.redis_state_client = None
        self._redis_stream_pool = None
        self._redis_state_pool = None
