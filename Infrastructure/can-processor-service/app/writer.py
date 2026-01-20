"""Writer for Redis Stream, TimescaleDB and Redis state keys."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import queue
import threading
import time
from typing import Any

import psycopg2
import psycopg2.extras
import redis

from shared.logging import get_logger

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
        db_config: dict[str, str] = None,
        redis_url: str = None,
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
            password = os.getenv("POSTGRES_PASSWORD")
            if not password:
                raise ValueError("POSTGRES_PASSWORD environment variable is required")
            self.db_config = {
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

        self.db_enabled = False
        self.redis_enabled = False

        # Cache for device and sensor lookups (avoid repeated queries)
        self.device_cache: dict[str, int] = {}  # {device_name: device_id}
        self.sensor_cache: dict[tuple[int, str], int] = {}  # {(device_id, sensor_name): sensor_id}

        # Async batching for DB writes
        self._db_queue = queue.Queue(maxsize=10000)
        self._flush_thread = None
        self._stop_flush = threading.Event()
        self._db_write_lock = threading.Lock()
        self._queued_count = 0
        self._flushed_count = 0
        self._dropped_count = 0

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
            self.db_conn.autocommit = True
            cursor = self.db_conn.cursor()
            cursor.execute("SET statement_timeout = '5000'")
            cursor.close()
            self.db_enabled = True
            logger.info("Connected to TimescaleDB (async batching enabled)")
            self._start_flush_thread()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to TimescaleDB: {e}")
            self.db_enabled = False
            return False

    def _start_flush_thread(self):
        if self._flush_thread and self._flush_thread.is_alive():
            return
        self._stop_flush.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="db-batch-flush", daemon=True
        )
        self._flush_thread.start()
        logger.info("Started DB batch flush thread (100ms interval, 50 msg threshold)")

    def _flush_loop(self):
        while not self._stop_flush.is_set():
            try:
                start_time = time.time()
                items = []
                while len(items) < 50:
                    remaining = 0.1 - (time.time() - start_time)
                    if remaining <= 0:
                        break
                    try:
                        item = self._db_queue.get(timeout=remaining)
                        items.append(item)
                        self._db_queue.task_done()
                    except queue.Empty:
                        break
                if items:
                    self._flush_batch(items)
            except Exception as e:
                logger.error(f"Error in DB flush loop: {e}")
                time.sleep(0.1)

    def _flush_batch(self, items):
        if not items or not self.db_enabled:
            return
        with self._db_write_lock:
            try:
                if not self._check_db_connection():
                    self._dropped_count += len(items)
                    return
                cursor = self.db_conn.cursor()
                measurements = []
                for item in items:
                    node_id = item.decoded.get("node_id")
                    if not node_id:
                        continue
                    device_name = f"Node {node_id}"
                    if device_name not in self.device_cache:
                        cursor.execute(
                            "SELECT device_id FROM device WHERE name = %s", (device_name,)
                        )
                        row = cursor.fetchone()
                        if not row:
                            continue
                        self.device_cache[device_name] = row[0]
                    device_id = self.device_cache[device_name]
                    for sensor_name, value, unit in item.sensors:
                        cache_key = (device_id, sensor_name)
                        if cache_key not in self.sensor_cache:
                            cursor.execute(
                                "SELECT sensor_id FROM sensor WHERE device_id = %s AND name = %s",
                                (device_id, sensor_name),
                            )
                            row = cursor.fetchone()
                            if not row:
                                continue
                            self.sensor_cache[cache_key] = row[0]
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
                    self._flushed_count += len(items)
                cursor.close()
            except Exception as e:
                logger.error(f"Error flushing batch: {e}")
                self._dropped_count += len(items)

    def _check_db_connection(self) -> bool:
        """Check if database connection is alive and reconnect if needed.

        Returns:
            True if connection is healthy or reconnection succeeded, False otherwise
        """
        if not self.db_conn:
            return self.connect_db()

        try:
            # Use a lightweight query to check connection health
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            logger.warning(f"Database connection lost, attempting reconnect: {e}")
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
            # Create connection pool for stream client (binary mode)
            stream_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=False,  # Keep binary for stream writes
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            self.redis_client = redis.Redis(connection_pool=stream_pool)
            self.redis_client.ping()

            # Create connection pool for state client (decode_responses=True)
            # This is reused across all write_to_redis_state calls instead of creating new clients
            state_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=True,  # Decode for state key operations
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            self.redis_state_client = redis.Redis(connection_pool=state_pool)
            self.redis_state_client.ping()

            self.redis_enabled = True
            logger.info(f"Connected to Redis at {self.redis_url} (with connection pooling)")
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

            # Write to Redis Stream with automatic trimming (keep last 100,000 messages)
            self.redis_client.xadd(self.stream_name, stream_data, maxlen=1100000, approximate=True)
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
        if not self.db_enabled:
            return False
        item = DBWriteItem(decoded=decoded, raw_data=raw_data, sensors=sensors, timestamp=timestamp)
        try:
            self._db_queue.put_nowait(item)
            self._queued_count += 1
            queue_size = self._db_queue.qsize()
            if queue_size > 8000 and queue_size % 1000 == 0:
                logger.warning(f"DB write queue depth high: {queue_size}/10000")
            return True
        except queue.Full:
            self._dropped_count += 1
            logger.error("DB write queue full, dropping measurement")
            return False

    def get_stats(self):
        return {
            "queued": self._queued_count,
            "flushed": self._flushed_count,
            "dropped": self._dropped_count,
            "pending": self._db_queue.qsize(),
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

        try:
            cursor = self.db_conn.cursor()
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
            for sensor_name, value, unit in sensors:
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

            # Use pipeline for batch operations
            pipe = self.redis_state_client.pipeline()

            for sensor_name, value, unit in sensors:
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
        self._stop_flush.set()
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)
        # Flush remaining
        remaining = []
        while not self._db_queue.empty():
            try:
                remaining.append(self._db_queue.get_nowait())
            except Exception:
                break
        if remaining:
            self._flush_batch(remaining)
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

        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception as e:
                logger.debug(f"Error closing Redis client: {e}")
            self.redis_client = None

        if self.redis_state_client:
            try:
                self.redis_state_client.close()
            except Exception as e:
                logger.debug(f"Error closing Redis state client: {e}")
            self.redis_state_client = None
