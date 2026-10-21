"""PID Controller Manager - Handles PID control logic for devices."""

from __future__ import annotations

from typing import Any

from app.control.decision_event_policy import DecisionEventPolicy
from app.control.pid_control_processing import PIDControlProcessingMixin
from app.control.pid_controller_state import PIDControllerStateMixin
from app.state import StateManager, get_state_manager


class PIDControllerManager(PIDControllerStateMixin, PIDControlProcessingMixin):
    """Manages PID controllers for device control."""

    def __init__(
        self, database_manager, event_policy: DecisionEventPolicy, config: Any | None = None
    ):
        """Initialize PID controller manager."""
        self.database = database_manager
        self.config = config
        self._pid_controllers: dict[tuple[str, str, str, str], Any] = {}
        self._settings_default_controllers: set[tuple[str, str, str, str]] = set()
        self._autotuners: dict[str, Any] = {}
        self._state: StateManager = get_state_manager()
        self._event_policy = event_policy
