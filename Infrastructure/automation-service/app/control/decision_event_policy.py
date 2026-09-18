"""Thresholded, non-blocking operational-event policy for control decisions."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from app.events.operational_models import (
    ControlPayload,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
)
from app.events.operational_ports import OperationalEventSink

OUTPUT_DELTA_PERCENT: Final = 5.0

# Missing-input lifecycle events only make sense for controllers that consume a
# live sensor reading. Rule-driven devices (lights) and manual contexts have no
# mapped sensor by design; dominating their input availability would emit noise.
INPUT_LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"control.input_missing", "control.input_recovered"}
)
SENSOR_DRIVEN_CONTROLLERS: Final[frozenset[str]] = frozenset({"pid", "on_off", "auto_pid", "vpd"})
NUMERICAL_EVENT_INTERVAL_SECONDS: Final = 30.0
RAPID_OUTPUT_DELTA_PERCENT: Final = 20.0
RAPID_SAMPLE_WINDOW_SECONDS: Final = 60.0
RAPID_SAMPLE_CAPACITY: Final = 256
# Meaningful setpoint-change thresholds per device type. Units differ by
# device: light is a 0-1 fraction, heating/cooling are degrees Celsius, VPD is
# kPa, and CO2 is ppm. Sub-threshold drift accumulates because the baseline
# only advances on emission. Unknown device types fall back to 0.01 absolute.
SETPOINT_CHANGE_THRESHOLDS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "light": 0.005,
        "heating": 0.1,
        "cooling": 0.1,
        "vpd": 0.02,
        "co2": 10.0,
    }
)
SETPOINT_CHANGE_FALLBACK_THRESHOLD: Final = 0.01


@dataclass(frozen=True, slots=True)
class DecisionObservation:
    """The values used by one control calculation or application decision."""

    location: str
    cluster: str
    device_name: str
    timestamp: datetime
    controller: str
    sensor_value: float | None
    effective_setpoint: float | None
    error: float | None
    output_percent: float | None
    control_mode: str
    reason_code: str
    reason_text: str
    device_type: str = ""
    ramping: bool = False


@dataclass(frozen=True, slots=True)
class _DecisionBaseline:
    output_percent: float | None
    observed_at: datetime
    control_mode: str
    effective_setpoint: float | None
    inputs_available: bool
    saturated: bool


@dataclass(frozen=True, slots=True)
class _NumericalSample:
    """One command output retained for endpoint movement reporting."""

    output_percent: float
    observed_at: datetime


class DecisionEventPolicy:
    """Suppress routine control noise while preserving meaningful lifecycle changes."""

    def __init__(self, sink: OperationalEventSink | None = None) -> None:
        self._sink = sink
        self._baselines: dict[tuple[str, str, str, str], _DecisionBaseline] = {}
        self._slow_baselines: dict[tuple[str, str, str, str], _DecisionBaseline] = {}
        self._samples: dict[tuple[str, str, str, str], deque[_NumericalSample]] = {}
        self._last_observed_at: dict[tuple[str, str, str, str], datetime] = {}

    def observe(self, observation: DecisionObservation) -> DecisionObservation:
        """Emit an eligible decision event without affecting the observed control result."""
        key = (
            observation.location,
            observation.cluster,
            observation.device_name,
            observation.controller,
        )
        timestamp = _as_utc(observation.timestamp)
        inputs_available = (
            observation.sensor_value is not None and observation.effective_setpoint is not None
        )
        saturated = observation.output_percent in (0.0, 100.0)
        previous = self._baselines.get(key)
        last_observed_at = self._last_observed_at.get(key)
        if last_observed_at is not None and timestamp < last_observed_at:
            self._reset_numerical_state(key)
            previous = None
        self._last_observed_at[key] = timestamp
        baseline = _DecisionBaseline(
            output_percent=observation.output_percent,
            observed_at=timestamp,
            control_mode=observation.control_mode,
            effective_setpoint=observation.effective_setpoint,
            inputs_available=inputs_available,
            saturated=saturated,
        )
        if previous is None:
            if observation.control_mode == "failsafe":
                self._emit(observation, "control.failsafe_entered")
            elif not inputs_available and observation.controller in SENSOR_DRIVEN_CONTROLLERS:
                self._emit(observation, "control.input_missing")
            self._baselines[key] = baseline
            self._slow_baselines[key] = baseline
            self._record_sample(key, observation.output_percent, timestamp, inputs_available)
            return observation

        event_type = _lifecycle_event_type(
            previous,
            baseline,
            _setpoint_change_threshold(observation.device_type),
        )
        if event_type is not None:
            # Ramp interpolation owns its full lifecycle (ramp.started/midpoint/
            # completed/interrupted, category ramp): effective-setpoint movement
            # while a ramp is advancing is not a "target setpoint changed" event.
            # The baseline still advances so no stale post-ramp emission leaks.
            suppress_event = (
                event_type in INPUT_LIFECYCLE_EVENT_TYPES
                and observation.controller not in SENSOR_DRIVEN_CONTROLLERS
            ) or (event_type == "control.setpoint_changed" and observation.ramping)
            if not suppress_event:
                self._emit(
                    observation,
                    event_type,
                    previous_setpoint=(
                        previous.effective_setpoint
                        if event_type == "control.setpoint_changed"
                        else None
                    ),
                )
            self._baselines[key] = baseline
            if (
                previous.control_mode != baseline.control_mode
                or previous.inputs_available != baseline.inputs_available
            ):
                self._reset_numerical_state(key)
                self._slow_baselines[key] = baseline
            self._record_sample(key, observation.output_percent, timestamp, inputs_available)
            return observation

        if not inputs_available:
            self._reset_numerical_state(key)
            return observation

        rapid = self._is_rapid_adjustment(
            key, observation.output_percent, timestamp, inputs_available
        )
        slow_baseline = self._slow_baselines[key]
        slow = _is_numerical_adjustment(slow_baseline, baseline)
        if rapid or slow:
            self._emit(observation, "control.rapid_adjusted" if rapid else "control.adjusted")
            self._baselines[key] = baseline
            self._slow_baselines[key] = baseline
            if rapid and observation.output_percent is not None:
                self._samples[key] = deque(
                    (_NumericalSample(observation.output_percent, timestamp),),
                    maxlen=RAPID_SAMPLE_CAPACITY,
                )
        if not rapid:
            self._record_sample(key, observation.output_percent, timestamp, inputs_available)
        return observation

    def _record_sample(
        self,
        key: tuple[str, str, str, str],
        output_percent: float | None,
        timestamp: datetime,
        inputs_available: bool,
    ) -> None:
        if output_percent is None or not inputs_available:
            if not inputs_available:
                self._reset_numerical_state(key)
            return
        samples = self._samples.setdefault(key, deque(maxlen=RAPID_SAMPLE_CAPACITY))
        cutoff = timestamp.timestamp() - RAPID_SAMPLE_WINDOW_SECONDS
        while samples and samples[0].observed_at.timestamp() < cutoff:
            samples.popleft()
        samples.append(_NumericalSample(output_percent, timestamp))

    def _is_rapid_adjustment(
        self,
        key: tuple[str, str, str, str],
        output_percent: float | None,
        timestamp: datetime,
        inputs_available: bool,
    ) -> bool:
        if output_percent is None or not inputs_available:
            return False
        samples = self._samples.get(key)
        if not samples:
            return False
        cutoff = timestamp.timestamp() - RAPID_SAMPLE_WINDOW_SECONDS
        eligible = next(
            (sample for sample in samples if sample.observed_at.timestamp() >= cutoff),
            None,
        )
        return (
            eligible is not None
            and abs(output_percent - eligible.output_percent) >= RAPID_OUTPUT_DELTA_PERCENT
        )

    def _reset_numerical_state(self, key: tuple[str, str, str, str]) -> None:
        self._samples.pop(key, None)
        self._slow_baselines.pop(key, None)

    def emit_lifecycle(self, observation: DecisionObservation, event_type: str) -> None:
        """Emit a calculation lifecycle event with the exact decision context."""
        self._emit(observation, event_type)

    def _emit(
        self,
        observation: DecisionObservation,
        event_type: str,
        *,
        previous_setpoint: float | None = None,
    ) -> None:
        if self._sink is None:
            return
        event = OperationalEvent(
            occurred_at=_as_utc(observation.timestamp),
            source=EventSource.AUTOMATION,
            category=EventCategory.CONTROL,
            severity=EventSeverity.INFO,
            event_type=event_type,
            entity=EntityContext(
                entity_type="device",
                entity_id=observation.device_name,
                location=observation.location,
                cluster=observation.cluster,
            ),
            reason_code=event_type if event_type != "control.adjusted" else observation.reason_code,
            reason_text=_reason_text(event_type, observation.reason_text),
            payload=ControlPayload(
                controller=observation.controller,
                device_type=observation.device_type or None,
                sensor_value=observation.sensor_value,
                effective_setpoint=observation.effective_setpoint,
                previous_setpoint=previous_setpoint,
                error=observation.error,
                output_percent=observation.output_percent,
            ),
        )
        try:
            self._sink.emit_nowait(event)
        except Exception:  # noqa: BLE001
            return


def _as_utc(timestamp: datetime) -> datetime:
    """Normalize the control clock to the event envelope's aware timestamp contract."""
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _setpoint_change_threshold(device_type: str) -> float:
    """Resolve the meaningful-change threshold for a device type."""
    return SETPOINT_CHANGE_THRESHOLDS.get(device_type, SETPOINT_CHANGE_FALLBACK_THRESHOLD)


def _lifecycle_event_type(
    previous: _DecisionBaseline,
    current: _DecisionBaseline,
    setpoint_change_threshold: float,
) -> str | None:
    if current.control_mode != previous.control_mode:
        if current.control_mode == "failsafe":
            return "control.failsafe_entered"
        if previous.control_mode == "failsafe":
            return "control.failsafe_cleared"
        return "control.mode_changed"
    if not current.inputs_available and previous.inputs_available:
        return "control.input_missing"
    if current.inputs_available and not previous.inputs_available:
        return "control.input_recovered"
    if current.effective_setpoint is None or previous.effective_setpoint is None:
        if current.effective_setpoint != previous.effective_setpoint:
            return "control.setpoint_changed"
    elif abs(current.effective_setpoint - previous.effective_setpoint) >= setpoint_change_threshold:
        return "control.setpoint_changed"
    if current.saturated and not previous.saturated:
        return "control.saturated"
    if previous.saturated and not current.saturated:
        return "control.recovered"
    return None


def _is_numerical_adjustment(previous: _DecisionBaseline, current: _DecisionBaseline) -> bool:
    if previous.output_percent is None or current.output_percent is None:
        return False
    output_delta = abs(current.output_percent - previous.output_percent)
    elapsed_seconds = (current.observed_at - previous.observed_at).total_seconds()
    return (
        output_delta >= OUTPUT_DELTA_PERCENT and elapsed_seconds >= NUMERICAL_EVENT_INTERVAL_SECONDS
    )


def _reason_text(event_type: str, fallback: str) -> str:
    if event_type == "control.rapid_adjusted":
        return f"Rapid control output movement; {fallback}"
    lifecycle_reasons = {
        "control.input_missing": "Control input is unavailable",
        "control.input_recovered": "Control input recovered",
        "control.failsafe_entered": "Failsafe control mode entered",
        "control.failsafe_cleared": "Failsafe control mode cleared",
        "control.mode_changed": "Control mode changed",
        "control.setpoint_changed": "Effective control setpoint changed",
        "control.saturated": "Control output reached saturation",
        "control.recovered": "Control output recovered from saturation",
    }
    return lifecycle_reasons.get(event_type, fallback)
