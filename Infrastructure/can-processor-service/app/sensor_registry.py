"""Registry-backed assignment authority for CAN ingestion.

The hot path never touches metadata storage:
``AssignmentCache.get`` is a nonblocking dict lookup and
``MetadataWorker.offer`` is a nonblocking bounded-queue put. A dedicated
metadata worker thread owns the psycopg connection, upserts observed
nodes, refreshes ``last_seen`` at most once per 30 s per node, and
refreshes assignments at most every 5 s. On metadata failure the last
good assignments stay cached and unknown nodes stay unassigned.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import psycopg2
import psycopg2.extensions

from shared.db_credentials import load_postgres_password
from shared.infra_logging import get_logger

logger = get_logger(__name__)

ASSIGNMENT_REFRESH_INTERVAL_SEC = 5.0
LAST_SEEN_REFRESH_INTERVAL_SEC = 30.0
METADATA_CONNECT_TIMEOUT_SEC = 1
METADATA_STATEMENT_TIMEOUT_MS = 1000
MAX_BACKOFF_SEC = 30.0
OBSERVATION_QUEUE_MAX = 1000
DISCOVERY_DRAIN_LIMIT = 200


@dataclass(frozen=True)
class SensorAssignment:
    """Commissioned placement snapshot for one physical CAN node."""

    registry_id: int
    hardware_address: int
    room: str
    cluster: str


class AssignmentCache:
    """Thread-safe in-memory assignment snapshot."""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._assignments: dict[int, SensorAssignment] = {}

    def get(self, node_id: int | None) -> SensorAssignment | None:
        """Nonblocking lookup; unassigned/unknown nodes return ``None``."""
        with self._lock:
            return self._assignments.get(node_id)

    def snapshot(self) -> dict[int, SensorAssignment]:
        with self._lock:
            return dict(self._assignments)

    def replace_all(self, assignments: dict[int, SensorAssignment]) -> None:
        with self._lock:
            self._assignments = dict(assignments)


class MetadataWorker:
    """Dedicated metadata worker thread with its own psycopg connection."""

    def __init__(
        self,
        cache: AssignmentCache,
        db_config: dict[str, Any] | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._cache = cache
        self._on_change = on_change
        # ``None`` defers credential loading to connect time so constructing
        # a DataWriter never requires database credentials by itself.
        self._db_config = db_config
        if self._db_config is None:
            self._db_config = {
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "database": os.getenv("POSTGRES_DB", "cea_sensors"),
                "user": os.getenv("POSTGRES_USER", "cea_user"),
            }
        self._observations: queue.Queue[int] = queue.Queue(maxsize=OBSERVATION_QUEUE_MAX)
        self._dropped_observations = 0
        self._last_seen_refreshed_at: dict[int, float] = {}
        self._backoff_sec = 1.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def offer(self, node_id: int | None) -> None:
        """Nonblocking observation offer; silently drops when bounded-queue full."""
        if node_id is None:
            return
        try:
            self._observations.put_nowait(node_id)
        except queue.Full:
            self._dropped_observations += 1
            if self._dropped_observations % 1000 == 0:
                logger.warning(
                    "sensor registry observation queue full; dropped %d total",
                    self._dropped_observations,
                )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="sensor-registry-metadata", daemon=True
        )
        self._thread.start()
        logger.info("Sensor registry metadata worker started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                self._cycle()
                self._backoff_sec = 1.0
            except Exception as exc:
                logger.warning(
                    "Sensor registry metadata failure, backing off %.1fs: %s",
                    self._backoff_sec,
                    exc,
                )
                self._backoff_sec = min(self._backoff_sec * 2.0, MAX_BACKOFF_SEC)
            elapsed = time.monotonic() - cycle_started
            self._stop_event.wait(max(0.0, min(self._backoff_sec, 1.0) - elapsed))

    def _cycle(self) -> None:
        connection = self._connect()
        try:
            self._drain_observations(connection)
            self._refresh_last_seen(connection)
            self._refresh_assignments(connection)
        finally:
            connection.close()

    def _connect(self) -> psycopg2.extensions.connection:
        connect_kwargs = dict(self._db_config)
        if "password" not in connect_kwargs:
            connect_kwargs["password"] = load_postgres_password()
        connection = psycopg2.connect(
            connect_timeout=METADATA_CONNECT_TIMEOUT_SEC, **connect_kwargs
        )
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(
            f"SET statement_timeout = '{METADATA_STATEMENT_TIMEOUT_MS * 1000}'"
        )
        cursor.close()
        return connection

    def _drain_observations(self, connection: psycopg2.extensions.connection) -> None:
        seen: set[int] = set()
        for _ in range(DISCOVERY_DRAIN_LIMIT):
            try:
                node_id = self._observations.get_nowait()
            except queue.Empty:
                break
            self._upsert_node(connection, node_id)
            seen.add(node_id)

        now = time.monotonic()
        for node_id in seen:
            self._last_seen_refreshed_at[node_id] = now

    def _upsert_node(
        self, connection: psycopg2.extensions.connection, node_id: int
    ) -> None:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO sensor_registry (bus, hardware_address, display_name)
            VALUES ('can', %s, %s)
            ON CONFLICT (bus, hardware_address) DO NOTHING
            """,
            (node_id, f"Node {node_id}"),
        )
        cursor.close()

    def _refresh_last_seen(self, connection: psycopg2.extensions.connection) -> None:
        now = time.monotonic()
        for node_id in list(self._last_seen_refreshed_at.keys()):
            refreshed_at = self._last_seen_refreshed_at[node_id]
            if now - refreshed_at < LAST_SEEN_REFRESH_INTERVAL_SEC:
                continue
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE sensor_registry
                SET last_seen = NOW()
                WHERE bus = 'can' AND hardware_address = %s
                """,
                (node_id,),
            )
            cursor.close()
            self._last_seen_refreshed_at[node_id] = now

    def _refresh_assignments(self, connection: psycopg2.extensions.connection) -> None:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT sr.registry_id, sr.hardware_address, sr.location_in_room, r.name AS room_name
            FROM sensor_registry sr
            LEFT JOIN room r ON r.room_id = sr.room_id
            WHERE sr.bus = 'can'
            """
        )
        rows = cursor.fetchall()
        cursor.close()

        assignments: dict[int, SensorAssignment] = {}
        for registry_id, hardware_address, location_in_room, room_name in rows:
            if location_in_room is None or room_name is None:
                continue
            assignments[int(hardware_address)] = SensorAssignment(
                registry_id=int(registry_id),
                hardware_address=int(hardware_address),
                room=str(room_name),
                cluster=str(location_in_room),
            )

        previous = self._cache.snapshot()
        self._cache.replace_all(assignments)
        if previous != assignments:
            logger.info("Sensor registry assignments refreshed: %d assigned", len(assignments))
            if self._on_change is not None:
                self._on_change()
