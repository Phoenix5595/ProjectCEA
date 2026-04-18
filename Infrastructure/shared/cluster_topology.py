"""Canonical room → cluster registry.

This is the **single source of truth** for the cluster topology
contract documented in ``ProjectCEA/AGENTS.md`` ("Cluster Topology
Contract"). The codebase distinguishes:

* **Device cluster** — room-wide actuator/relay/dimmer set. Always
  named ``"main"``. Every room has exactly one device cluster.
* **Sensor sub-cluster** — physically separated sensor groupings.
  Currently only ``Flower Room`` is split into ``front`` / ``back``
  (because of its dual-bench layout). Other rooms expose a single
  sensor cluster also called ``"main"``.

Why a separate module
---------------------
Before Phase 5e, the same room→cluster knowledge was duplicated in at
least nine places (``backend/app/database.py``,
``backend/app/routes/sensors.py``, ``backend/app/routes/live.py``,
``backend/app/stream_processor.py``, ``backend/app/background_tasks.py``,
``automation-service/app/cluster_config.py``,
``automation-service/app/routes/devices.py``,
``frontend/src/config/zones.ts``, plus several Grafana SQL panels).
That duplication produced two recurring bugs:

1. Endpoints silently returning empty payloads when the caller passed
   the wrong cluster type (e.g. ``GET /api/sensors/Flower Room/main``).
2. The frontend dashboard polling the *device* endpoint with
   *sensor* sub-cluster names (``front``/``back``), producing 404 noise
   in the browser console for every refresh tick.

Both classes of bug are eliminated by:

* Routing every cluster decision in Python services through this
  module.
* Mirroring it in the frontend at
  ``Infrastructure/frontend/src/config/clusterTopology.ts``.
* Making the API endpoints return **400** (with a hint message) on a
  cross-type lookup instead of an empty dict.

If you change anything in this module — add a room, split a room into
sub-clusters, etc. — also update the TS mirror and ``ProjectCEA/AGENTS.md``.
The two files are intentionally tiny so the duplication is cheap; CI
does not yet enforce the parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class _RoomTopology:
    """Per-room topology entry.

    ``device_cluster`` is the cluster name used by every actuator API
    (``/api/devices/{room}/{cluster}``, ``/api/lights/...``,
    ``/api/control/...``). It is always ``"main"`` today; the field
    exists so the contract is explicit at the call site rather than
    being a magic string.

    ``sensor_clusters`` is the *ordered* tuple of clusters used by
    sensor APIs (``/api/sensors/{room}/{cluster}``). For Flower Room
    this is ``("front", "back")``; for every other room it is
    ``("main",)``. Order matters for UI display (front first) and for
    deterministic iteration when fanning out polls.

    ``sensor_suffix_for`` resolves the SQL ``LIKE`` pattern segment
    used to filter ``sensor.name`` rows down to a single sub-cluster.
    Returning ``None`` means "no suffix filter — return every sensor
    in this room" (used for Lab/Outside where there is no naming
    convention split). The pattern is intentionally only the **suffix
    fragment** (e.g. ``"_f"``); callers wrap it as ``"%" + fragment``
    when building SQL.
    """

    device_cluster: str
    sensor_clusters: tuple[str, ...]
    # Map sensor sub-cluster → SQL suffix fragment. ``None`` → no filter.
    sensor_suffixes: dict[str, str | None]


# ---------------------------------------------------------------------------
# Canonical registry. Add new rooms / re-shape existing ones HERE and nowhere
# else. Then update the TS mirror at
# ``frontend/src/config/clusterTopology.ts`` to match.
# ---------------------------------------------------------------------------
_TOPOLOGY: Final[dict[str, _RoomTopology]] = {
    "Flower Room": _RoomTopology(
        device_cluster="main",
        sensor_clusters=("front", "back"),
        sensor_suffixes={"front": "_f", "back": "_b"},
    ),
    "Veg Room": _RoomTopology(
        device_cluster="main",
        sensor_clusters=("main",),
        # Veg sensors are tagged with ``_v`` suffix so the same
        # ``measurement`` table can hold every room's sensors and still
        # be filtered cheaply.
        sensor_suffixes={"main": "_v"},
    ),
    "Lab": _RoomTopology(
        device_cluster="main",
        sensor_clusters=("main",),
        # Lab sensor names have no suffix convention (the room only has
        # one cluster, so no disambiguation is needed). ``None`` means
        # "match any sensor in this room".
        sensor_suffixes={"main": None},
    ),
    "Outside": _RoomTopology(
        device_cluster="main",
        sensor_clusters=("main",),
        sensor_suffixes={"main": None},
    ),
}


# ---------------------------------------------------------------------------
# Public API. Keep this small and stable; everything in the codebase that
# needs cluster knowledge should call one of these helpers rather than
# reaching into ``_TOPOLOGY`` directly.
# ---------------------------------------------------------------------------
class UnknownRoomError(KeyError):
    """Raised when a caller passes a room name that isn't registered."""


class ClusterMismatchError(ValueError):
    """Raised when a cluster name is the wrong *type* for the room.

    Carries a ``hint`` attribute with the human-readable correction
    message that should be surfaced to API clients (e.g. as the
    ``detail`` of a FastAPI ``HTTPException(400)``).
    """

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.hint = hint


def known_rooms() -> tuple[str, ...]:
    """Return every registered room, in registry order."""
    return tuple(_TOPOLOGY.keys())


def _get(room: str) -> _RoomTopology:
    try:
        return _TOPOLOGY[room]
    except KeyError as exc:
        raise UnknownRoomError(f"Unknown room {room!r}; known rooms: {sorted(_TOPOLOGY)}") from exc


def device_cluster_for(room: str) -> str:
    """Return the device cluster name for ``room`` (always ``"main"`` today)."""
    return _get(room).device_cluster


def sensor_clusters_for(room: str) -> tuple[str, ...]:
    """Return the ordered tuple of sensor cluster names for ``room``."""
    return _get(room).sensor_clusters


def is_device_cluster(room: str, cluster: str) -> bool:
    """True if ``cluster`` is the *device* cluster of ``room``."""
    return _get(room).device_cluster == cluster


def is_sensor_cluster(room: str, cluster: str) -> bool:
    """True if ``cluster`` is one of ``room``'s *sensor* sub-clusters."""
    return cluster in _get(room).sensor_clusters


def sensor_name_like_pattern(room: str, cluster: str) -> str | None:
    """Return the SQL ``LIKE`` pattern for ``room``/``cluster`` sensors.

    * ``"%_f"`` for ``Flower Room/front``.
    * ``"%_b"`` for ``Flower Room/back``.
    * ``"%_v"`` for ``Veg Room/main``.
    * ``None`` for rooms without a suffix convention (Lab / Outside) —
      caller should *not* add a ``LIKE`` clause and should match every
      sensor in the room instead.

    Raises ``ClusterMismatchError`` if ``cluster`` is not a sensor
    cluster for ``room``.
    """
    topo = _get(room)
    if cluster not in topo.sensor_suffixes:
        raise ClusterMismatchError(
            f"{cluster!r} is not a sensor cluster for {room!r}",
            hint=_sensor_hint(room, cluster, topo),
        )
    suffix = topo.sensor_suffixes[cluster]
    return None if suffix is None else f"%{suffix}"


def assert_sensor_cluster(room: str, cluster: str) -> None:
    """Raise ``ClusterMismatchError`` unless ``cluster`` is a sensor cluster."""
    topo = _get(room)
    if cluster not in topo.sensor_clusters:
        raise ClusterMismatchError(
            f"{cluster!r} is not a sensor cluster for {room!r}",
            hint=_sensor_hint(room, cluster, topo),
        )


def assert_device_cluster(room: str, cluster: str) -> None:
    """Raise ``ClusterMismatchError`` unless ``cluster`` is the device cluster."""
    topo = _get(room)
    if cluster != topo.device_cluster:
        raise ClusterMismatchError(
            f"{cluster!r} is not the device cluster for {room!r}",
            hint=(
                f"{room!r} exposes one device cluster: "
                f"{topo.device_cluster!r}. "
                f"Sensor sub-clusters {list(topo.sensor_clusters)} are "
                f"reachable via /api/sensors/{room}/<sub-cluster>."
            ),
        )


def _sensor_hint(room: str, cluster: str, topo: _RoomTopology) -> str:
    """Build the operator-friendly correction message for a wrong cluster."""
    if cluster == topo.device_cluster and topo.sensor_clusters != (cluster,):
        # Caller asked for a device cluster on a sensor endpoint AND the
        # room has named sub-clusters — point them at the sub-clusters.
        return (
            f"{cluster!r} is the device cluster for {room!r}; "
            f"sensor data lives under {list(topo.sensor_clusters)}. "
            f"Try /api/sensors/{room}/{topo.sensor_clusters[0]}."
        )
    return (
        f"{cluster!r} is not a sensor cluster for {room!r}; "
        f"valid options: {list(topo.sensor_clusters)}."
    )


__all__ = [
    "ClusterMismatchError",
    "UnknownRoomError",
    "assert_device_cluster",
    "assert_sensor_cluster",
    "device_cluster_for",
    "is_device_cluster",
    "is_sensor_cluster",
    "known_rooms",
    "sensor_clusters_for",
    "sensor_name_like_pattern",
]
