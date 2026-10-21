"""Observation and current-snapshot helpers for the control engine."""

from __future__ import annotations

from datetime import UTC, datetime
import inspect
import math
from typing import Any

from app.control.current_publication_observer import build_current_snapshot
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.schemas.monitoring_models import (
    Phase,
    PhotoperiodObservationSink,
    RuntimeSnapshotVersion,
)
from app.services.photoperiod_history_logger import PhotoperiodObservation
from shared.infra_logging import get_logger
from shared.monitoring_contracts import CurrentSnapshot

logger = get_logger("app.control.control_engine")


class ObservationMixin:
    def _get_active_room_mode(
        self, location: str, cluster: str, snapshot: RuntimeDeviceSnapshot
    ) -> dict[str, Any] | None:
        """Read one active identity from the immutable snapshot captured for this tick."""
        view = snapshot.active_modes.get((location, cluster))
        if view is None:
            return None
        return {"location": location, "cluster": cluster, **dict(view)}

    def _enqueue_final_photoperiod_phase(
        self,
        *,
        location: str,
        cluster: str,
        active_mode: dict[str, Any] | None,
        phase: Phase,
        snapshot: RuntimeDeviceSnapshot,
        observed_at: datetime,
    ) -> None:
        """Offer an exact phase to history without delaying the control decision."""
        sink = self.photoperiod_observation_sink
        if sink is None:
            return
        sink.enqueue_final_phase(
            PhotoperiodObservation(
                observed_at=observed_at.astimezone(UTC),
                location=location,
                cluster=cluster,
                phase=phase,
                mode_id=(active_mode or {}).get("mode_id"),
                submode_id=(active_mode or {}).get("submode_id"),
                runtime_snapshot_version=RuntimeSnapshotVersion(snapshot.version),
            )
        )

    def _mark_snapshot_rooms_unavailable(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Mark every room of the failed tick's snapshot unavailable for recording."""
        sink = self.photoperiod_observation_sink
        if sink is None:
            return
        for room_key in self._snapshot_room_keys(snapshot):
            self._safe_mark_unavailable(sink, room_key, snapshot.version)

    def _mark_removed_rooms_unavailable(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Mark rooms absent from this successful tick compared with the prior one."""
        sink = self.photoperiod_observation_sink
        if sink is None:
            return
        current = set(self._snapshot_room_keys(snapshot))
        previous = self._last_tick_rooms
        self._last_tick_rooms = current
        if previous is None:
            return
        for room_key in sorted(previous - current):
            self._safe_mark_unavailable(sink, room_key, snapshot.version)

    @staticmethod
    def _snapshot_room_keys(
        snapshot: RuntimeDeviceSnapshot,
    ) -> list[tuple[str, str]]:
        """List the location/cluster pairs the snapshot resolves."""
        return [
            (location, cluster)
            for location, clusters in snapshot.hierarchy.items()
            for cluster in clusters
        ]

    def _safe_mark_unavailable(
        self,
        sink: PhotoperiodObservationSink,
        room_key: tuple[str, str],
        version: int,
    ) -> None:
        """Keep history-marking faults outside the control tick's success domain."""
        try:
            sink.mark_unavailable(
                location=room_key[0],
                cluster=room_key[1],
                observed_at=datetime.now(UTC),
                runtime_snapshot_version=RuntimeSnapshotVersion(version),
            )
        except Exception as error:
            logger.warning(
                "Photoperiod coverage could not be marked unavailable for %s/%s: %s",
                room_key[0],
                room_key[1],
                error,
            )

    def _ramp_remaining_seconds_at(
        self, observed_at: datetime
    ) -> dict[tuple[str, str], dict[str, float]]:
        """Capture RAM ramp deadlines for known nominal targets at one observation instant."""
        ramp_manager = getattr(self.setpoint_manager, "ramp_manager", None)
        active_ramps = getattr(ramp_manager, "active_ramps", None)
        if active_ramps is None:
            return {}

        metrics = ("heating", "cooling", "vpd", "co2")
        remaining_by_room: dict[tuple[str, str], dict[str, float]] = {}
        active_ramp_keys: set[tuple[str, str, str]] = set()
        for ramp_key, ramp in active_ramps.items():
            if ramp_key[2] not in metrics or ramp.is_complete(observed_at):
                continue
            active_ramp_keys.add(ramp_key)
            remaining = (ramp.end_time - observed_at).total_seconds()
            if math.isfinite(remaining) and remaining > 0:
                room_key = (ramp_key[0], ramp_key[1])
                remaining_by_room.setdefault(room_key, {})[ramp_key[2]] = remaining

        for room_key, setpoints in self._tick_effective_setpoints.items():
            for metric in metrics:
                nominal = setpoints.get(f"nominal_{metric}_setpoint")
                if (
                    isinstance(nominal, bool)
                    or not isinstance(nominal, int | float)
                    or not math.isfinite(nominal)
                ):
                    continue
                if (*room_key, metric) not in active_ramp_keys:
                    remaining_by_room.setdefault(room_key, {})[metric] = 0.0
        return remaining_by_room

    def _build_current_snapshot(
        self, snapshot: RuntimeDeviceSnapshot, observed_at: datetime
    ) -> CurrentSnapshot | None:
        """Capture facts consumed by the completed tick without reading external state."""
        return build_current_snapshot(
            effective_setpoints=self._tick_effective_setpoints,
            automation_context=self._automation_context,
            relay_states=self.relay_manager.get_all_states(),
            photoperiod_phases=self._photoperiod_phases,
            runtime_snapshot_version=snapshot.version,
            observed_at=observed_at,
            active_profiles=snapshot.active_modes,
            ramp_remaining_seconds=self._ramp_remaining_seconds_at(observed_at),
        )

    def _offer_current_snapshot(
        self, snapshot: RuntimeDeviceSnapshot, observed_at: datetime
    ) -> None:
        """Keep observer faults outside the control loop's success domain."""
        observer = self.control_tick_observer
        if observer is None:
            return
        try:
            current = self._build_current_snapshot(snapshot, observed_at)
            if current is None:
                return
            offered = observer.offer(current)
            if inspect.isawaitable(offered):
                if inspect.iscoroutine(offered):
                    offered.close()
                raise TypeError("Control tick observers must offer synchronously")
        except BaseException:
            self._current_observer_failures += 1
