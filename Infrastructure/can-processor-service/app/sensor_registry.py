"""Registry-backed assignment authority for CAN ingestion.

The hot path never touches metadata storage:
``AssignmentCache.get`` is a nonblocking dict lookup and
``MetadataWorker.offer`` is a nonblocking bounded-queue put. A dedicated
metadata worker thread owns the psycopg connection and upserts observed
nodes, refreshing ``last_seen`` only when a frame is received. Assignments
refresh each metadata cycle. On metadata failure the last good assignments stay
cached and unknown nodes stay unassigned.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
import os
import queue
import threading
import time
from typing import Any

import psycopg2
import psycopg2.extensions

from shared.db_credentials import load_postgres_password
from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Ten minutes lets short CAN outages recover without mistaking a replacement
# for the same physical identity.
ABSENCE = timedelta(minutes=10)

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
                "database": os.getenv("POSTGRES_DB", "sj_cea"),
                "user": os.getenv("POSTGRES_USER", "cea_user"),
            }
        self._observations: queue.Queue[int] = queue.Queue(maxsize=OBSERVATION_QUEUE_MAX)
        self._dropped_observations = 0
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
        cursor.execute(f"SET statement_timeout = '{METADATA_STATEMENT_TIMEOUT_MS * 1000}'")
        cursor.close()
        return connection

    def _drain_observations(self, connection: psycopg2.extensions.connection) -> None:
        identity_changed = False
        seen_node_ids: set[int] = set()
        node_ids: list[int] = []
        for _ in range(DISCOVERY_DRAIN_LIMIT):
            try:
                node_id = self._observations.get_nowait()
            except queue.Empty:
                break
            if node_id not in seen_node_ids:
                seen_node_ids.add(node_id)
                node_ids.append(node_id)

        for node_id in node_ids:
            identity_changed = self._upsert_node(connection, node_id) or identity_changed

        if identity_changed and self._on_change is not None:
            self._on_change()

    def _ensure_registry_device(self, cursor: Any, registry_id: int, node_id: int) -> bool:
        cursor.execute(
            """
            SELECT sr.device_id, sr.sensor_uid, sr.display_name, target.rack_id
            FROM sensor_registry sr
            LEFT JOIN rack target
              ON target.room_id = sr.room_id
             AND target.name = CASE sr.location_in_room
                   WHEN 'front' THEN 'Front'
                   WHEN 'back' THEN 'Back'
                   WHEN 'main' THEN 'Main'
                 END
            WHERE sr.registry_id = %s
            FOR UPDATE OF sr
            """,
            (registry_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"CAN registry record {registry_id} disappeared")
        device_id, sensor_uid, display_name, rack_id = row
        if device_id is not None:
            return False

        cursor.execute(
            """
            INSERT INTO device (rack_id, name, type, serial_number)
            VALUES (%s, %s, 'sensor-node', 'SENSOR-' || %s::text)
            RETURNING device_id
            """,
            (rack_id, display_name or f"Node {node_id}", sensor_uid),
        )
        device_id = cursor.fetchone()[0]
        cursor.execute(
            """
            UPDATE sensor_registry
            SET device_id = %s, updated_at = clock_timestamp()
            WHERE registry_id = %s AND device_id IS NULL
            """,
            (device_id, registry_id),
        )
        return True

    def _insert_registry_row(
        self,
        cursor: Any,
        node_id: int,
        observed_at: Any,
        identity_status: str,
        candidate_id: int | None,
        pending_since: Any,
    ) -> int:
        cursor.execute(
            """
            INSERT INTO sensor_registry (
                bus, hardware_address, display_name, identity_status,
                pending_candidate_registry_id, pending_since,
                first_seen, last_seen, updated_at
            )
            VALUES ('can', %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING registry_id
            """,
            (
                node_id,
                f"Node {node_id}",
                identity_status,
                candidate_id,
                pending_since,
                observed_at,
                observed_at,
                observed_at,
            ),
        )
        registry_id = int(cursor.fetchone()[0])
        cursor.execute(
            """
            INSERT INTO sensor_address_history (registry_id, bus, hardware_address, valid_from)
            VALUES (%s, 'can', %s, %s)
            """,
            (registry_id, node_id, observed_at),
        )
        self._ensure_registry_device(cursor, registry_id, node_id)
        return registry_id

    def _upsert_node(self, connection: psycopg2.extensions.connection, node_id: int) -> bool:
        """Upsert one observed address and detach a confirmed stale identity."""
        previous_autocommit = connection.autocommit
        connection.autocommit = False
        cursor = None
        changed = False
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT clock_timestamp()")
            observed_at = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT registry_id, identity_status, last_seen
                FROM sensor_registry
                WHERE bus = 'can' AND hardware_address = %s
                FOR UPDATE
                """,
                (node_id,),
            )
            current = cursor.fetchone()

            if current is not None:
                registry_id, identity_status, last_seen = current
                if identity_status == "confirmed" and last_seen < observed_at - ABSENCE:
                    self._ensure_registry_device(cursor, int(registry_id), node_id)
                    cursor.execute(
                        """
                        UPDATE sensor_address_history
                        SET valid_to = %s
                        WHERE registry_id = %s AND valid_to IS NULL
                        """,
                        (observed_at, registry_id),
                    )
                    cursor.execute(
                        """
                        UPDATE sensor_registry
                        SET hardware_address = NULL, identity_status = 'detached',
                            pending_candidate_registry_id = NULL, pending_since = NULL,
                            updated_at = %s
                        WHERE registry_id = %s
                        """,
                        (observed_at, registry_id),
                    )
                    self._insert_registry_row(
                        cursor,
                        node_id,
                        observed_at,
                        "pending",
                        int(registry_id),
                        observed_at,
                    )
                    changed = True
                else:
                    changed = self._ensure_registry_device(cursor, int(registry_id), node_id)
                    cursor.execute(
                        """
                        UPDATE sensor_registry
                        SET last_seen = %s, updated_at = %s
                        WHERE registry_id = %s
                        """,
                        (observed_at, observed_at, registry_id),
                    )
            else:
                cursor.execute(
                    """
                    SELECT registry_id
                    FROM sensor_registry
                    WHERE bus = 'can' AND device_id IS NOT NULL
                      AND identity_status IN ('confirmed', 'detached')
                      AND last_seen < %s::timestamptz - %s::interval
                    ORDER BY registry_id
                    """,
                    (observed_at, ABSENCE),
                )
                stale = cursor.fetchall()
                identity_status = "pending" if stale else "confirmed"
                candidate_id = int(stale[0][0]) if len(stale) == 1 else None
                self._insert_registry_row(
                    cursor,
                    node_id,
                    observed_at,
                    identity_status,
                    candidate_id,
                    observed_at if stale else None,
                )
                changed = True

            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise
        finally:
            if cursor is not None:
                cursor.close()
            connection.autocommit = previous_autocommit


    def _refresh_assignments(self, connection: psycopg2.extensions.connection) -> None:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT sr.registry_id, sr.hardware_address, sr.location_in_room, r.name AS room_name
            FROM sensor_registry sr
            LEFT JOIN room r ON r.room_id = sr.room_id
            WHERE sr.bus = 'can' AND sr.hardware_address IS NOT NULL
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
