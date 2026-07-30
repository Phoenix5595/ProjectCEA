"""Strict device-registry projections shared by API and runtime reads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging
from typing import Any, assert_never

from pydantic import ValidationError

from ...models.device_registry import Device, LightDevice
from ._helpers import _row_to_typed_device

logger = logging.getLogger(__name__)

DeviceHierarchy = dict[str, dict[str, dict[str, dict[str, Any]]]]
RegistryDevice = Device | LightDevice


@dataclass(frozen=True, slots=True)
class RegistryProjection:
    """Typed registry rows and the legacy hierarchy derived from those rows."""

    flat: tuple[RegistryDevice, ...]
    hierarchy: DeviceHierarchy


def project_registry_rows(rows: Iterable[Mapping[str, Any]]) -> RegistryProjection:
    """Parse valid registry rows once and derive every public read projection from them."""
    devices = tuple(
        device for row in rows if (device := _parse_registry_device(dict(row))) is not None
    )
    hierarchy: DeviceHierarchy = {}
    for device in devices:
        hierarchy.setdefault(device.location, {}).setdefault(device.cluster, {})[
            device.device_name
        ] = _hierarchy_device_info(device)
    return RegistryProjection(flat=devices, hierarchy=hierarchy)


def _parse_registry_device(row: dict[str, Any]) -> RegistryDevice | None:
    """Return a valid typed row, excluding legacy rows that fail model validation."""
    try:
        return _row_to_typed_device(row)
    except ValidationError as error:
        logger.warning(
            "Excluding invalid device registry row device_id=%s: %s", row.get("device_id"), error
        )
        return None


def _hierarchy_device_info(device: RegistryDevice) -> dict[str, Any]:
    """Adapt a typed device model to the established runtime hierarchy shape."""
    match device:
        case LightDevice():
            return {
                **device.model_dump(exclude={"board_id", "relay_channel"}),
                "channel": device.relay_channel,
                "dimming_board_id": device.board_id,
            }
        case Device():
            return device.model_dump()
        case unreachable:
            assert_never(unreachable)
