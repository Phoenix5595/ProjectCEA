"""Shared utilities for all CEA Infrastructure services.

Only re-export modules whose dependency footprint is **pure-stdlib** here.
Anything with a heavy third-party dep must be imported directly from its
submodule instead — otherwise every ``from shared.X import Y`` triggers
``shared/__init__.py``, which would crash any venv that doesn't carry that
heavy dep.

Concrete example: ``shared.db`` imports ``asyncpg``. The ``can-processor``
and ``onewire-worker`` venvs (Redis-only services) deliberately do *not*
ship asyncpg. Re-exporting ``create_pool`` here would make
``from shared.infra_logging import get_logger`` raise ``ModuleNotFoundError:
asyncpg`` in those venvs. Don't do it.

Allowed here today:
  - ``shared.infra_logging`` (only ``logging``/``re``/``json``)
  - ``shared.climate`` (only ``math``)
  - ``shared.pressure_state`` (only ``collections``)
  - ``shared.sensor_validation`` (only ``datetime``)

Import these directly from their submodules:
  - ``from shared.db import create_pool, db_config_from_env``  # asyncpg
  - ``from shared.cluster_topology import ...``                # yaml
  - ``from shared.retry import retry_async``                   # asyncio (ok
    standalone, but consumers tend to be already-async services anyway)
"""

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
