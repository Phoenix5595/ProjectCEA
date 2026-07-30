"""Shared location and cluster checks against the canonical topology registry."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from shared.cluster_topology import is_device_cluster, known_rooms


def ensure_configured_cluster(
    devices_config: dict[str, Any] | None, location: str, cluster: str
) -> None:
    """Raise 404/400 if the location/cluster is not usable for control APIs.

    Device identity and assignments now live in the PostgreSQL registry, but
    the set of known rooms and the device cluster name (always ``main``) are
    still canonicalized in ``shared.cluster_topology``. This validation no
    longer depends on the legacy YAML ``devices`` section.
    """
    if location not in known_rooms():
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
    if not is_device_cluster(location, cluster):
        raise HTTPException(
            status_code=400,
            detail=f"{cluster!r} is not the device cluster for {location!r}; use 'main'.",
        )
