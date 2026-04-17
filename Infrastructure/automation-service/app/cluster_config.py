"""Shared (location, cluster) checks against the YAML ``devices:`` tree."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def iter_flower_main_merged_devices(
    location_config: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(source_cluster, device_name, device_info)`` for Flower control plane ``main``.

    Includes legacy ``front``/``back`` device namespaces until YAML is merged on disk.
    """
    out: list[tuple[str, str, dict[str, Any]]] = []
    for src_cluster in ("main", "front", "back"):
        block = location_config.get(src_cluster)
        if not isinstance(block, dict):
            continue
        for device_name, device_info in block.items():
            if isinstance(device_info, dict):
                out.append((src_cluster, device_name, device_info))
    return out


def ensure_configured_cluster(
    devices_config: dict[str, Any] | None, location: str, cluster: str
) -> None:
    """Raise 404 if the location/cluster is not usable for control APIs.

    Flower Room control plane is ``main``; legacy YAML may still list ``front``/``back`` until
    startup merge moves equipment into ``main``. Treat ``main`` as valid if any of
    ``main``/``front``/``back`` exists under ``Flower Room``.
    """
    devices_config = devices_config or {}
    if location not in devices_config:
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
    loc = devices_config[location]
    if not isinstance(loc, dict):
        raise HTTPException(status_code=404, detail="Unknown location/cluster")
    if cluster in loc:
        return
    if location == "Flower Room" and cluster == "main":
        if any(k in loc for k in ("main", "front", "back")):
            return
    raise HTTPException(status_code=404, detail="Unknown location/cluster")
