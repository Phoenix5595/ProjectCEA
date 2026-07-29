from __future__ import annotations

from collections.abc import Generator
from typing import Any


def iter_devices_flat(
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]],
    location: str | None = None,
    cluster: str | None = None,
    device_type: str | None = None,
) -> Generator[tuple[str, str, str, dict[str, Any]], None, None]:
    for current_location, clusters in devices.items():
        if location is not None and current_location != location:
            continue
        for current_cluster, device_entries in clusters.items():
            if cluster is not None and current_cluster != cluster:
                continue
            for device_name, device_info in device_entries.items():
                if device_type is not None and device_info.get("device_type") != device_type:
                    continue
                yield current_location, current_cluster, device_name, device_info
