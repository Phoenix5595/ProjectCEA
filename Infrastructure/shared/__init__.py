"""Shared utilities for all CEA Infrastructure services."""

from __future__ import annotations

from shared.climate import (
    calculate_rh,
    calculate_rh_from_dewpoint,
    calculate_vpd,
)
from shared.infra_logging import (
    ConsoleFormatter,
    JsonFormatter,
    LoggingContext,
    StructuredLogger,
    get_logger,
    setup_structured_logging,
)
from shared.pressure_state import (
    SEA_LEVEL_HPA,
    get_pressure_state,
    update_pressure_state,
)
from shared.sensor_validation import validate_co2_reading

__all__ = [
    # Logging.
    "JsonFormatter",
    "ConsoleFormatter",
    "LoggingContext",
    "StructuredLogger",
    "get_logger",
    "setup_structured_logging",
    # Climate math (Phase 6 lift; see shared/climate.py).
    "calculate_rh",
    "calculate_vpd",
    "calculate_rh_from_dewpoint",
    # Pressure-state tracker (Phase 6 lift; see shared/pressure_state.py).
    "SEA_LEVEL_HPA",
    "get_pressure_state",
    "update_pressure_state",
    # Sensor validation (Phase 6 lift; see shared/sensor_validation.py).
    "validate_co2_reading",
]
