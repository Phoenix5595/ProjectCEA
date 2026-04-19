"""Lightweight sensor-reading validators (CO2, …).

Lifted out of ``can-processor-service/app/processor.py`` where it lived as
an inline fallback shim (the ``from shared import validate_co2_reading``
path always failed because nothing in ``shared/`` ever exported the name).

This is the canonical implementation now. It is intentionally very simple
— ``value >= 0`` is the only check today, matching the long-running
behavior. The hook exists primarily so future filtering logic
(stale-reading detection, range clamping, sensor-fault scoring) has one
obvious place to live.
"""

from __future__ import annotations

from datetime import datetime


def validate_co2_reading(sensor_name: str, value: float, timestamp: datetime) -> bool:
    """Return True if a CO2 reading should be accepted into the data pipeline.

    Args:
        sensor_name: Sensor key (e.g. ``"co2_b"``). Not currently consulted
            but kept in the signature so future per-sensor rules don't break
            the call sites.
        value: The CO2 reading in ppm.
        timestamp: When the reading was taken. Not currently consulted; kept
            for the same forward-compat reason.

    Returns:
        ``True`` to accept, ``False`` to drop. The current rule rejects
        negative values (the SCD30 occasionally emits a transient ``0`` or
        negative on bus glitches, but downstream consumers also tolerate
        ``0``, so we only filter clearly-impossible readings).
    """
    return value >= 0


__all__ = ["validate_co2_reading"]
