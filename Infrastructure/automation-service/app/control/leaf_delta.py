"""Time-varying leaf temperature delta interpolation.

Smoothly transitions between day and night leaf delta values based on
current mode and transition progress.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum


class ClimateMode(Enum):
    DAY = "day"
    NIGHT = "night"
    PRE_DAY = "pre_day"
    PRE_NIGHT = "pre_night"


def get_leaf_delta(
    current_mode: ClimateMode,
    leaf_delta_day: float,
    leaf_delta_night: float,
    transition_progress: float = 0.0,
) -> float:
    """Get current leaf temperature delta based on mode and transition.

    Args:
        current_mode: Current climate mode
        leaf_delta_day: Leaf delta for day mode (typically -1.5 to -3.0)
        leaf_delta_night: Leaf delta for night mode (typically -0.5 to -1.5)
        transition_progress: 0.0-1.0 progress through PRE_DAY or PRE_NIGHT ramp

    Returns:
        Current leaf delta value
    """
    if current_mode == ClimateMode.DAY:
        return leaf_delta_day
    elif current_mode == ClimateMode.NIGHT:
        return leaf_delta_night
    elif current_mode == ClimateMode.PRE_DAY:
        # Transitioning from night to day
        # Start with night delta, end with day delta
        return leaf_delta_night + (leaf_delta_day - leaf_delta_night) * transition_progress
    elif current_mode == ClimateMode.PRE_NIGHT:
        # Transitioning from day to night
        # Start with day delta, end with night delta
        return leaf_delta_day + (leaf_delta_night - leaf_delta_day) * transition_progress
    else:
        # Default to day delta
        return leaf_delta_day


def calculate_transition_progress(
    mode_start_time: datetime,
    transition_duration_minutes: int,
    current_time: datetime | None = None,
) -> float:
    """Calculate progress through a mode transition.

    Args:
        mode_start_time: When the current mode started
        transition_duration_minutes: Total duration of transition
        current_time: Current time (defaults to now)

    Returns:
        Progress 0.0 to 1.0 (clamped)
    """
    if current_time is None:
        current_time = datetime.now()

    if transition_duration_minutes <= 0:
        return 1.0

    elapsed = (current_time - mode_start_time).total_seconds() / 60.0
    progress = elapsed / transition_duration_minutes

    return max(0.0, min(1.0, progress))


class LeafDeltaManager:
    """Manages leaf temperature delta with smooth transitions."""

    def __init__(self, default_day_delta: float = -2.0, default_night_delta: float = -1.0):
        self.default_day_delta = default_day_delta
        self.default_night_delta = default_night_delta

        self._current_mode = ClimateMode.DAY
        self._mode_start_time = datetime.now()
        self._transition_duration = 0

        # Per-room overrides
        self._room_deltas = {}

    def set_room_deltas(self, location: str, cluster: str, day_delta: float, night_delta: float):
        """Set leaf deltas for a specific room/cluster."""
        key = (location, cluster)
        self._room_deltas[key] = (day_delta, night_delta)

    def get_room_deltas(self, location: str, cluster: str) -> tuple[float, float]:
        """Get leaf deltas for a room (day, night)."""
        key = (location, cluster)
        return self._room_deltas.get(key, (self.default_day_delta, self.default_night_delta))

    def set_mode(self, mode: ClimateMode, transition_duration_minutes: int = 0):
        """Set current mode and start transition timer."""
        self._current_mode = mode
        self._mode_start_time = datetime.now()
        self._transition_duration = transition_duration_minutes

    def get_current_delta(self, location: str, cluster: str) -> float:
        """Get current interpolated leaf delta for a room."""
        day_delta, night_delta = self.get_room_deltas(location, cluster)

        progress = 1.0
        if self._current_mode in (ClimateMode.PRE_DAY, ClimateMode.PRE_NIGHT):
            progress = calculate_transition_progress(
                self._mode_start_time, self._transition_duration
            )

        return get_leaf_delta(self._current_mode, day_delta, night_delta, progress)
