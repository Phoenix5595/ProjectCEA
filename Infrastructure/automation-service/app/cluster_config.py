"""Shared location and cluster checks against registry device snapshots."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def ensure_configured_cluster(
    devices_config: dict[str, Any] | None, location: str, cluster: str
) -> None:
    """Raise 404 if the location/cluster is not usable for control APIs.

    The device registry uses ``main`` for every room's device cluster.
    """
    devices_config = devices_config or {}
    if location not in devices_config:
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
    loc = devices_config[location]
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
    if cluster not in loc:
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
