"""Control engine that orchestrates schedules and PID control."""

from __future__ import annotations

from collections.abc import Coroutine
from datetime import datetime
from typing import Any

from app.alarm_manager import AlarmManager
from app.config import ConfigLoader
from app.control.climate_resolver import ClimatePeriodResolver
from app.control.control_engine_history import HistoryMixin
from app.control.control_engine_observation import ObservationMixin
from app.control.control_engine_tick import TickMixin
from app.control.current_publication_observer import ControlTickObserver
from app.control.decision_event_policy import DecisionEventPolicy
from app.control.device_command_service import DeviceCommandService
from app.control.device_controller import DeviceController
from app.control.device_processor import DeviceProcessor
from app.control.engine_config_cache import EngineConfigCache
from app.control.pid_controller_manager import PIDControllerManager
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.relay_manager import RelayManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.scheduler import Scheduler
from app.control.sensor_reader import SensorReader
from app.control.setpoint_calculator import SetpointCalculator
from app.control.setpoint_manager import SetpointManager
from app.control.vpd_cascade_controller import VPDCascadeController
from app.database import DatabaseManager
from app.events.operational_ports import OperationalEventSink
from app.schemas.monitoring_models import PhotoperiodObservationSink
from app.state import StateManager, get_state_manager
from shared.infra_logging import get_logger
from shared.monitoring_contracts import PhotoperiodPhase
from shared.replay_buffer import ReplayBuffer

logger = get_logger(__name__)


class ControlEngine(ObservationMixin, TickMixin, HistoryMixin):
    """Main control engine that executes automation logic."""

    def __init__(
        self,
        relay_manager: RelayManager,
        database: DatabaseManager,
        config: ConfigLoader,
        scheduler: Scheduler,
        runtime_device_registry: RuntimeDeviceRegistry,
        event_policy: DecisionEventPolicy,
        relay_board_state_manager: RelayBoardStateManager | None = None,
        alarm_manager: AlarmManager | None = None,
        dfr0971_manager: Any | None = None,  # DFR0971Manager (avoid circular import)
        device_command_service: DeviceCommandService | None = None,
        photoperiod_observation_sink: PhotoperiodObservationSink | None = None,
        control_tick_observer: ControlTickObserver | None = None,
        event_sink: OperationalEventSink | None = None,
    ):
        """Initialize control engine.

        Args:
            relay_manager: Relay manager instance
            database: Database manager instance
            config: Config loader instance
            scheduler: Scheduler instance
            alarm_manager: Optional alarm manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity logging
        """
        self.relay_manager = relay_manager
        self.database = database
        self.config = config
        self.scheduler = scheduler
        self.alarm_manager = alarm_manager
        self.dfr0971_manager = dfr0971_manager
        self.runtime_device_registry = runtime_device_registry
        self.relay_board_state_manager = relay_board_state_manager
        self.device_command_service = device_command_service
        self.photoperiod_observation_sink = photoperiod_observation_sink
        self.control_tick_observer = control_tick_observer
        self.decision_event_policy = event_policy
        self._current_observer_failures = 0
        # Successful-tick room keys for comparing snapshot spills between attempts.
        self._last_tick_rooms: set[tuple[str, str]] | None = None

        # Initialize extracted components
        self.pid_controller_manager = PIDControllerManager(
            database, event_policy=event_policy, config=config
        )
        control_cfg = config.get_control_config() if config is not None else {}
        self.device_controller = DeviceController(
            relay_manager=relay_manager,
            database_manager=database,
            event_policy=event_policy,
            dfr0971_manager=dfr0971_manager,
            binary_hysteresis=float(control_cfg.get("binary_hysteresis", 0.1)),
        )

        # VPD Cascade Controller for intelligent actuator selection (create before DeviceProcessor)
        self.vpd_cascade_controller = VPDCascadeController(
            vpd_deadband=0.05,  # 0.05 kPa deadband
            kp=20.0,
            ki=0.5,
            kd=2.0,
        )

        # Initialize new extracted components
        self.device_processor = DeviceProcessor(
            self.device_controller,
            database,
            dfr0971_manager,
            scheduler,
            pid_controller_manager=self.pid_controller_manager,
            vpd_cascade_controller=self.vpd_cascade_controller,
            device_command_service=device_command_service,
        )

        # StateManager for fast in-memory state access (<1ms reads)
        self._state: StateManager = get_state_manager()

        # Initialize SetpointManager AFTER _state is defined
        self.setpoint_manager = SetpointManager(
            database=database,
            state_manager=self._state,
            event_sink=event_sink,
        )

        # Initialize pipeline components
        self.sensor_reader = SensorReader(database, self._state, config)
        self.climate_resolver = ClimatePeriodResolver(scheduler, self.setpoint_manager, self._state)
        self.setpoint_calculator = SetpointCalculator()

        # This context is published live; history logging uses its separate context below.
        self._automation_context: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._automation_log_context: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._last_logged: dict[tuple[str, str, str], tuple[tuple[Any, ...], int]] = {}
        self._automation_state_pending: ReplayBuffer[dict[str, Any]] = ReplayBuffer(
            2_000, name="automation_state"
        )
        self._photoperiod_phases: dict[tuple[str, str], PhotoperiodPhase] = {}

        # Track current climate mode per location/cluster
        self._current_climate_mode: dict[tuple[str, str], str] = {}

        # Track current climate period per location/cluster (for period transition detection)
        self._current_period_name: dict[tuple[str, str], str] = {}

        # Moon-authority rooms (drying, sleep): scheduled lighting is MOON; manual override still allowed.
        self._moon_authority_forced_moon: set[tuple[str, str]] = set()

        # Track effective setpoints per location/cluster
        # Format: (location, cluster) -> {
        #   'effective_heating_setpoint': float,
        #   'effective_cooling_setpoint': float,
        #   'nominal_heating_setpoint': float,
        #   'nominal_cooling_setpoint': float,
        #   'ramp_progress_heating': float or None,
        #   'ramp_progress_cooling': float or None
        # }
        self._effective_setpoints: dict[tuple[str, str], dict[str, Any]] = {}
        self._tick_effective_setpoints: dict[tuple[str, str], dict[str, Any]] = {}

        # Light effective setpoint logging interval (seconds per device).
        # With 1s control tick and 6 dimmers (3 DFR0971 boards × 2 channels):
        #   6 devices × 1/10s = 0.6 writes/s to effective_setpoints hypertable.
        # TimescaleDB compression after 7 days keeps storage manageable.
        # REVIEW: 2026-05-31 — 10s chosen for faster diagnostic visibility.
        # Original was 60s. If DB write throughput becomes a concern, revert to 30s or 60s.
        self._last_light_effective_log: dict[tuple[str, str, str], datetime] = {}
        self._light_effective_log_interval_sec = 10
        self._last_light_sun_schedule_gap_error: dict[tuple[str, str, str], float] = {}

        # Config snapshot cache (device tree + sensor mapping, shared TTL)
        self._config_cache = EngineConfigCache(ttl_seconds=30.0)

        # Performance profiling
        self._profiling_enabled = True
        self._performance_stats: dict[str, list[float]] = {
            "total_loop_time": [],
            "sensor_reading_time": [],
            "setpoint_calculation_time": [],
            "device_processing_time": [],
        }
        self._max_stats_history = 100  # Keep last 100 measurements

        self._pending_db_writes: list[Coroutine[Any, Any, Any]] = []

        logger.info("Control engine initialized")

    def _record_performance_stat(self, key: str, value: float) -> None:
        """Record a performance measurement."""
        if not self._profiling_enabled:
            return

        stats_list = self._performance_stats[key]
        stats_list.append(value)
        if len(stats_list) > self._max_stats_history:
            stats_list.pop(0)
