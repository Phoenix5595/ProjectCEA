"""Canonical room → cluster registry.

This is the **single source of truth** for the cluster topology
contract documented in ``ProjectCEA/AGENTS.md`` ("Cluster Topology
Contract"). Two distinct concepts share the URL slot ``{cluster}`` and
must not be mixed up:

* **Device cluster** — room-wide actuator / relay / dimmer namespace.
  Always named ``"main"``. Every room has exactly one. This is the
  cluster identifier used by the actuator plane: ``/api/devices/...``,
  ``/api/lights/...``, ``/api/control/...``.

* **Sensor sub-cluster** — *physically* distinct sensor groupings.
  Only ``Flower Room`` has any (``front`` and ``back``, because of its
  dual-bench layout). ``Veg Room``, ``Lab`` and ``Outside`` have NO
  sensor sub-clusters: their sensors live directly under the room.

Hierarchy (the part the docs and AGENTS.md repeat):

* Device cluster ``main`` is the **parent**.
* Sensor sub-clusters ``front`` / ``back`` are **children** of Flower's
  ``main``. They never appear on the device plane; the device plane
  rejects them with a 400.
* ``main`` is a **device-cluster name only** — it is never registered
  as a sensor sub-cluster. The only place ``main`` appears in a sensor
  URL is for unsplit rooms (Veg / Lab / Outside), where the URL slot
  reuses the device-cluster name as a *room-wide sentinel* meaning
  "this room has no sub-grouping". That reuse is a transport detail of
  ``/api/sensors/{room}/{cluster}``, not a sensor sub-cluster
  registration. ``sensor_subclusters_for("Veg Room")`` returns ``()``
  precisely so this distinction stays visible in code.

API contract enforced (Phase 5e + 5f clarification):

* ``GET /api/devices/{room}/{cluster}`` — ``cluster`` MUST be the
  room's device cluster (``main``). Sensor sub-cluster names → 400.
* ``GET /api/sensors/{room}/{cluster}`` — for rooms with sensor
  sub-clusters: ``cluster`` MUST be one of them (``front`` / ``back``
  for Flower); the device-cluster name → 400. For rooms with no
  sensor sub-clusters: ``cluster`` MUST be the device-cluster sentinel
  (``main``); anything else → 400.
* Unknown room name → 404.

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

from dataclasses import dataclass, field
from typing import Final

# ---------------------------------------------------------------------------
# Room prefix registry (used for canonical device_name generation)
# ---------------------------------------------------------------------------
_ROOM_PREFIXES: dict[str, str] = {
    "Flower Room": "f",
    "Veg Room": "v",
    "Lab": "l",
    "Outside": "o",
}


def _room_prefix(room: str) -> str:
    """Return the one-letter prefix for a room name."""
    if room not in _ROOM_PREFIXES:
        raise ValueError(f"Unknown room: {room!r}")
    return _ROOM_PREFIXES[room]


@dataclass(frozen=True)
class _RoomTopology:
    """Per-room topology entry.

    ``device_cluster`` is the cluster name used by every actuator API
    (``/api/devices/{room}/{cluster}``, ``/api/lights/...``,
    ``/api/control/...``). It is always ``"main"`` today; the field
    exists so the contract is explicit at the call site rather than
    being a magic string.

    ``sensor_subclusters`` is the *ordered* tuple of **physical** sensor
    sub-cluster identifiers under this room. For Flower Room it is
    ``("front", "back")``; for every other room it is ``()`` — those
    rooms have no physical sub-grouping. ``main`` is **never** a member
    of this tuple, by design, because ``main`` is a device-cluster
    identifier and the two namespaces are kept separate.

    ``sensor_subcluster_suffixes`` maps each sub-cluster identifier to
    the SQL ``LIKE`` suffix fragment used to select that sub-cluster's
    rows out of the shared ``measurement`` table (e.g. ``"_f"`` for
    Flower front). Only sub-clusters listed in ``sensor_subclusters``
    have an entry.
    """

    device_cluster: str
    sensor_subclusters: tuple[str, ...] = ()
    sensor_subcluster_suffixes: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Canonical registry. Add new rooms / re-shape existing ones HERE and nowhere
# else. Then update the TS mirror at
# ``frontend/src/config/clusterTopology.ts`` to match.
# ---------------------------------------------------------------------------
_TOPOLOGY: Final[dict[str, _RoomTopology]] = {
    # Flower Room: dual-bench layout, two sub-clusters defined.
    #
    # Wiring status (as of 2026-04): only `back` is physically connected
    # and producing telemetry; `front` is configured in code/dashboards
    # but its sensor harness is not in service yet. We deliberately keep
    # `front` in the topology — removing it would (a) break the
    # dashboard layout the operator already uses for the planned wiring,
    # and (b) hide the contract that `back` is one of two clusters, not
    # the only one. The frontend's `[flower-cluster-warning]` log entry
    # for `front` is the expected runtime signal of the unwired state.
    # When the front sensors come online, no code change is required.
    "Flower Room": _RoomTopology(
        device_cluster="main",
        sensor_subclusters=("front", "back"),
        sensor_subcluster_suffixes={"front": "_f", "back": "_b"},
    ),
    # Veg / Lab / Outside have a single device cluster (`main`) and NO
    # physical sensor sub-grouping. The sensor URL for these rooms uses
    # `main` as a sentinel (see `sensor_url_clusters_for`), not as a
    # registered sub-cluster.
    "Veg Room": _RoomTopology(device_cluster="main"),
    "Lab": _RoomTopology(device_cluster="main"),
    "Outside": _RoomTopology(device_cluster="main"),
}


# Veg Room sensor names use a `_v` suffix in the shared measurement table.
# We keep it out of the topology dataclass because Veg has no sub-cluster
# split (the suffix is a per-room *naming convention*, not a sub-cluster
# discriminator). `sensor_name_like_pattern` consults this map for unsplit
# rooms; missing entry → no LIKE filter.
_ROOM_NAMING_SUFFIX: Final[dict[str, str]] = {
    "Veg Room": "_v",
    # Lab / Outside have no naming-convention suffix.
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


def sensor_subclusters_for(room: str) -> tuple[str, ...]:
    """Return the ordered tuple of *physical* sensor sub-clusters for ``room``.

    Empty tuple for rooms without a physical sub-grouping (Veg / Lab /
    Outside). Use :func:`sensor_url_clusters_for` if you need the URL
    slugs to fan out polling instead — that helper substitutes the
    device-cluster sentinel for unsplit rooms.
    """
    return _get(room).sensor_subclusters


def sensor_url_clusters_for(room: str) -> tuple[str, ...]:
    """Return the URL ``{cluster}`` slugs accepted by ``/api/sensors/{room}/...``.

    For rooms with sub-clusters, this is the same tuple as
    :func:`sensor_subclusters_for`. For unsplit rooms (Veg / Lab /
    Outside) it is ``(device_cluster,)`` — the device-cluster name
    reused as a sentinel so the URL shape stays uniform across rooms.

    This is the helper the frontend should iterate when fanning out
    sensor-plane polls; the API validator (:func:`assert_sensor_cluster`)
    accepts exactly this set.
    """
    topo = _get(room)
    if topo.sensor_subclusters:
        return topo.sensor_subclusters
    return (topo.device_cluster,)


# ---- Back-compat alias ----------------------------------------------------
# Old call sites import ``sensor_clusters_for``; keep the name working but
# prefer the more precise ``sensor_url_clusters_for`` for new code.
def sensor_clusters_for(room: str) -> tuple[str, ...]:
    """Deprecated alias for :func:`sensor_url_clusters_for`."""
    return sensor_url_clusters_for(room)


def is_device_cluster(room: str, cluster: str) -> bool:
    """True if ``cluster`` is the *device* cluster of ``room``."""
    return _get(room).device_cluster == cluster


def is_sensor_subcluster(room: str, cluster: str) -> bool:
    """True if ``cluster`` is one of ``room``'s *physical* sensor sub-clusters.

    Returns ``False`` for the device-cluster sentinel — even on unsplit
    rooms where ``cluster == "main"`` is a valid sensor URL slug.
    """
    return cluster in _get(room).sensor_subclusters


# Old name kept for compat with route validators / tests.
def is_sensor_cluster(room: str, cluster: str) -> bool:
    """True if ``cluster`` is a valid sensor URL slug for ``room``."""
    return cluster in sensor_url_clusters_for(room)


def sensor_name_like_pattern(room: str, cluster: str) -> str | None:
    """Return the SQL ``LIKE`` pattern for ``room``/``cluster`` sensors.

    * ``"%_f"`` for ``Flower Room/front``.
    * ``"%_b"`` for ``Flower Room/back``.
    * ``"%_v"`` for ``Veg Room/main`` (room-level naming-convention
      suffix; ``main`` is the device-cluster sentinel for unsplit
      rooms — see module docstring).
    * ``None`` for rooms without a naming-convention suffix
      (``Lab/main``, ``Outside/main``); the caller should NOT add a
      ``LIKE`` clause and should match every sensor in the room.

    Raises :class:`ClusterMismatchError` if ``cluster`` is not a valid
    sensor URL slug for ``room`` (i.e. not in
    :func:`sensor_url_clusters_for`). Validation runs first — the
    function never produces a "looks-fine but matches nothing" filter
    from a wrong cluster.
    """
    assert_sensor_cluster(room, cluster)
    topo = _get(room)
    if topo.sensor_subclusters:
        # Cluster is guaranteed to be in the sub-cluster suffix map;
        # otherwise the assert above would have raised.
        return f"%{topo.sensor_subcluster_suffixes[cluster]}"
    suffix = _ROOM_NAMING_SUFFIX.get(room)
    return None if suffix is None else f"%{suffix}"


def assert_sensor_cluster(room: str, cluster: str) -> None:
    """Raise :class:`ClusterMismatchError` unless ``cluster`` is a sensor URL slug.

    "Sensor URL slug" means: a member of
    :func:`sensor_url_clusters_for` — i.e. a real sub-cluster
    (``front`` / ``back`` for Flower) OR the device-cluster sentinel
    (``main``) for unsplit rooms.
    """
    valid = sensor_url_clusters_for(room)
    if cluster not in valid:
        topo = _get(room)
        raise ClusterMismatchError(
            f"{cluster!r} is not a sensor cluster for {room!r}",
            hint=_sensor_hint(room, cluster, topo, valid),
        )


def assert_device_cluster(room: str, cluster: str) -> None:
    """Raise :class:`ClusterMismatchError` unless ``cluster`` is the device cluster.

    Device cluster is always ``main`` today. Sensor sub-cluster names
    (``front`` / ``back``) are explicitly rejected on the device plane;
    this is the check that turns the previous "404 with generic
    message" into a 400 with an actionable hint.
    """
    topo = _get(room)
    if cluster != topo.device_cluster:
        if topo.sensor_subclusters:
            hint = (
                f"{room!r} has one device cluster ({topo.device_cluster!r}); "
                f"{list(topo.sensor_subclusters)} are sensor sub-clusters and "
                f"are reachable via /api/sensors/{room}/<sub-cluster>, "
                f"never via /api/devices."
            )
        else:
            hint = (
                f"{room!r} has one device cluster ({topo.device_cluster!r}). "
                f"Use /api/devices/{room}/{topo.device_cluster}."
            )
        raise ClusterMismatchError(
            f"{cluster!r} is not the device cluster for {room!r}",
            hint=hint,
        )


def _sensor_hint(
    room: str,
    cluster: str,
    topo: _RoomTopology,
    valid: tuple[str, ...],
) -> str:
    """Build the operator-friendly correction message for a wrong cluster."""
    # Most common mistake: caller asked for the device cluster on a
    # sub-cluster room (e.g. /api/sensors/Flower Room/main).
    if cluster == topo.device_cluster and topo.sensor_subclusters:
        return (
            f"{cluster!r} is the device cluster for {room!r}; "
            f"sensor data lives under {list(topo.sensor_subclusters)}. "
            f"Try /api/sensors/{room}/{topo.sensor_subclusters[0]}."
        )
    # Caller passed a sub-cluster name to an unsplit room.
    if not topo.sensor_subclusters:
        return (
            f"{room!r} has no sensor sub-clusters; use /api/sensors/{room}/{topo.device_cluster}."
        )
    # Generic: name not in the valid sub-cluster set.
    return f"{cluster!r} is not a sensor cluster for {room!r}; valid options: {list(valid)}."


__all__ = [
    "ClusterMismatchError",
    "UnknownRoomError",
    "_ROOM_PREFIXES",
    "_room_prefix",
    "assert_device_cluster",
    "assert_sensor_cluster",
    "device_cluster_for",
    "is_device_cluster",
    "is_sensor_cluster",
    "is_sensor_subcluster",
    "known_rooms",
    "sensor_clusters_for",
    "sensor_name_like_pattern",
    "sensor_subclusters_for",
    "sensor_url_clusters_for",
]
