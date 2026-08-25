from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
import json
from typing import Any

from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
)
from shared.monitoring_contracts import ConfigVersion


@dataclass(frozen=True, slots=True)
class FrozenRow(Mapping[str, Any]):
    pairs: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        return dict(self.pairs)[key]

    def __iter__(self):
        return (key for key, _ in self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)


@dataclass(frozen=True, slots=True)
class MonitoringSnapshot:
    range: MonitoringRange
    location: str
    cluster: str
    active_mode: FrozenRow | None
    calendar_events: tuple[FrozenRow, ...]
    calendar_applications: tuple[FrozenRow, ...]
    climate_periods: tuple[FrozenRow, ...]
    mode_parameters: FrozenRow | None
    light_targets: tuple[FrozenRow, ...]
    light_programs: tuple[FrozenRow, ...]
    expected_lights: tuple[FrozenRow, ...]
    effective_setpoint_predecessors: tuple[FrozenRow, ...]
    ramp_anchors: tuple[FrozenRow, ...]
    automation_state_predecessors: tuple[FrozenRow, ...]
    photoperiod_predecessor: FrozenRow | None
    source_cursors: tuple[tuple[str, int | None], ...]
    projection_revision: ProjectionRevision
    anchor_fingerprint: AnchorFingerprint
    anchor_observed_at: datetime
    anchor_quality: Quality
    anchor_valid_until: datetime
    runtime_snapshot_version: RuntimeSnapshotVersion
    config_version: ConfigVersion | None = None


def frozen(row: Mapping[str, Any] | None) -> FrozenRow | None:
    return None if row is None else FrozenRow(tuple(sorted(dict(row).items())))


def frozen_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[FrozenRow, ...]:
    return tuple(row for item in rows if (row := frozen(item)) is not None)


def fingerprint(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode()).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date | time):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value
