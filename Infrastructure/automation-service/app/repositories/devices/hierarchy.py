"""Hierarchy traversal helpers for device repositories.

Provides synchronous and asynchronous generators for flattening the nested
device hierarchy, plus static helpers for grouping and re-shaping device lists.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import TYPE_CHECKING, Any

from app.models.device_registry import Device, LightDevice

if TYPE_CHECKING:
    from app.repositories.devices import DeviceRepository


def iter_devices_flat(
    devices: dict[str, dict[str, dict[str, dict[str, Any]]]],
    location: str | None = None,
    cluster: str | None = None,
    device_type: str | None = None,
) -> Generator[tuple[str, str, str, dict[str, Any]], None, None]:
    """Yield (location, cluster, device_name, device_info) from a hierarchy dict.

    Optional filters narrow the result set without changing the traversal order.
    """
    for loc, clusters in devices.items():
        if location is not None and loc != location:
            continue
        for clu, devs in clusters.items():
            if cluster is not None and clu != cluster:
                continue
            for name, info in devs.items():
                if device_type is not None and info.get("device_type") != device_type:
                    continue
                yield loc, clu, name, info


class HierarchyMixin:
    """Mixin adding hierarchy traversal and grouping methods to DeviceRepository."""

    async def iter_all_devices_flat(
        self,
        location: str | None = None,
        cluster: str | None = None,
        device_type: str | None = None,
    ) -> AsyncGenerator[tuple[str, str, str, dict[str, Any]], None]:
        """Yield (location, cluster, device_name, device_info) from the DB hierarchy.

        Optional filters narrow the result set without changing the traversal order.
        """
        hierarchy = await self.get_all_as_hierarchy()
        for loc, clu, name, info in iter_devices_flat(hierarchy, location, cluster, device_type):
            yield loc, clu, name, info

    @staticmethod
    def by_location(devices: list[Device | LightDevice]) -> dict[str, list[Device | LightDevice]]:
        """Group a flat list of devices by location."""
        result: dict[str, list[Device | LightDevice]] = {}
        for device in devices:
            result.setdefault(device.location, []).append(device)
        return result

    @staticmethod
    def to_hierarchy_dict(
        devices: list[Device | LightDevice],
    ) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Convert a flat list of typed devices back to the legacy nested dict shape.

        This is a backward-compatibility helper for consumers that have not yet
        been migrated to the typed list.
        """
        hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for device in devices:
            loc = device.location
            clu = device.cluster
            name = device.device_name
            hierarchy.setdefault(loc, {}).setdefault(clu, {})[name] = device.model_dump()
        return hierarchy


async def _find_displaced_device(
    device_repo: DeviceRepository,
    channel: int,
    exclude_device_id: int | None = None,
) -> int | None:
    """Return the device_id of a device already occupying ``channel``, or None."""
    async for _loc, _clu, _dev_name, dev_info in device_repo.iter_all_devices_flat():
        if dev_info.get("channel") == channel:
            dev_id = dev_info.get("device_id")
            if exclude_device_id is not None and dev_id == exclude_device_id:
                continue
            return dev_id
    return None
