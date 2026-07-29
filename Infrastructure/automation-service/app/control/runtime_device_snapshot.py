"""Immutable projections used by every runtime device-control consumer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

DeviceKey: TypeAlias = tuple[str, str, str]
# Legacy control consumers accept nested dictionaries. The installed value is
# recursively frozen by ``_freeze`` before publication.
DeviceHierarchy: TypeAlias = dict[str, dict[str, dict[str, dict[str, Any]]]]
DeviceInfo: TypeAlias = Mapping[str, Any]
ModeParameters: TypeAlias = Mapping[tuple[str, str], Mapping[str, Any]]
LightIntensityProjection: TypeAlias = Mapping[tuple[int, int], float]
LightProgramProjection: TypeAlias = tuple[Mapping[str, Any], ...]


def _freeze(value: Any) -> Any:
    """Recursively convert mutable DB projections into immutable runtime values."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class RuntimeDeviceSnapshot:
    """One complete, immutable view of device and light-control configuration."""

    version: int
    hierarchy: DeviceHierarchy
    by_device: Mapping[DeviceKey, int]
    by_channel: Mapping[int, DeviceKey]
    device_info: Mapping[DeviceKey, DeviceInfo]
    mode_parameters: ModeParameters
    light_intensities: LightIntensityProjection
    light_programs: LightProgramProjection
    light_programs_by_room: Mapping[tuple[str, str], LightProgramProjection]

    @classmethod
    def create(
        cls,
        *,
        version: int,
        hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]],
        mode_parameters: dict[tuple[str, str], dict[str, Any]],
        light_intensities: dict[tuple[int, int], float],
        light_programs: list[dict[str, Any]],
    ) -> RuntimeDeviceSnapshot:
        """Build all projections before exposing the snapshot to consumers."""
        by_device: dict[DeviceKey, int] = {}
        by_channel: dict[int, DeviceKey] = {}
        device_info: dict[DeviceKey, DeviceInfo] = {}

        for location, clusters in hierarchy.items():
            for cluster, devices in clusters.items():
                for device_name, info in devices.items():
                    key = (location, cluster, device_name)
                    frozen_info = _freeze(info)
                    device_info[key] = frozen_info
                    device_id = info.get("device_id")
                    if isinstance(device_id, int):
                        by_device[key] = device_id
                    channel = info.get("channel")
                    if isinstance(channel, int):
                        if channel in by_channel:
                            raise RuntimeError(
                                f"Duplicate relay channel in registry snapshot: {channel}"
                            )
                        by_channel[channel] = key

        frozen_programs = tuple(_freeze(program) for program in light_programs)
        programs_by_room: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for program in frozen_programs:
            location = program.get("location")
            cluster = program.get("cluster")
            if isinstance(location, str) and isinstance(cluster, str):
                programs_by_room.setdefault((location, cluster), []).append(program)

        return cls(
            version=version,
            hierarchy=_freeze(hierarchy),
            by_device=MappingProxyType(by_device),
            by_channel=MappingProxyType(by_channel),
            device_info=MappingProxyType(device_info),
            mode_parameters=_freeze(mode_parameters),
            light_intensities=MappingProxyType(dict(light_intensities)),
            light_programs=frozen_programs,
            light_programs_by_room=MappingProxyType(
                {key: tuple(programs) for key, programs in programs_by_room.items()}
            ),
        )
