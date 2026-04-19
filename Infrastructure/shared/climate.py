"""Pure-function climate calculations: RH and VPD from wet/dry bulb pairs.

Single source of truth for the wet-bulb-depression family of formulas used by
the CAN sensor pipeline. Lifted out of ``can-processor-service/app/processor.py``
and ``backend/app/stream_processor.py`` (which both carried byte-identical
duplicates of the same code, plus a broken ``from shared import calculate_rh``
that always fell through to the duplicate).

These functions are intentionally pure: same inputs → same outputs, no I/O,
no globals. State (e.g. last observed pressure for a location/cluster) lives
in ``shared.pressure_state``; callers pass the looked-up pressure in.

Formulas
--------
Saturation vapor pressure uses the Magnus-Tetens approximation
(a = 17.27, b = 237.3 °C), which matches what the legacy code used and is
accurate to well under 0.1 % over typical greenhouse temperatures.

Wet-bulb depression correction follows the standard psychrometric form
(see e.g. NOAA NWS technical note on wet-bulb temperature):

    e = e_s(T_w) - p · A · (T - T_w) · (1 + B · T_w)

where ``A = 6.6e-4`` 1/°C and ``B = 1.15e-3`` 1/°C are the wet-bulb
psychrometer constants and ``p`` is the actual ambient pressure (kPa).

The two functions differ only in units for the saturation pressure base
(``calculate_rh`` returns RH in %, derived from hPa-scaled e_s; ``calculate_vpd``
returns VPD in kPa, derived from kPa-scaled e_s). The legacy code split them
this way and both sets of unit conventions are baked into downstream consumers
(Grafana dashboards, automation thresholds), so we preserve the split here
rather than refactor the calling sides too.
"""

from __future__ import annotations

import math

# Magnus-Tetens coefficients.
_A: float = 17.27
_B: float = 237.3

# Psychrometer wet-bulb-depression constants.
_PSYCH_A: float = 0.00066
_PSYCH_B: float = 0.00115


def _saturation_hpa(temp_c: float) -> float:
    """Saturation vapor pressure in hectopascals (hPa) at ``temp_c``."""
    return 6.1078 * math.exp((_A * temp_c) / (_B + temp_c))


def _saturation_kpa(temp_c: float) -> float:
    """Saturation vapor pressure in kilopascals (kPa) at ``temp_c``."""
    return 0.6108 * math.exp((_A * temp_c) / (_B + temp_c))


def calculate_rh(temp_dry_c: float, temp_wet_c: float, pressure_hpa: float = 1013.25) -> float:
    """Relative humidity (%) from a wet/dry bulb pair.

    Args:
        temp_dry_c: Dry-bulb temperature in °C.
        temp_wet_c: Wet-bulb temperature in °C.
        pressure_hpa: Ambient pressure in hectopascals (default = sea level).

    Returns:
        RH clamped to [0, 100]. Returns 100.0 when ``temp_dry_c <= temp_wet_c``
        (saturated air; wet bulb cannot exceed dry bulb in reality, but sensor
        noise can briefly invert them).
    """
    if temp_dry_c <= temp_wet_c:
        return 100.0
    es_dry = _saturation_hpa(temp_dry_c)
    es_wet = _saturation_hpa(temp_wet_c)
    e = es_wet - (
        (pressure_hpa / 1000.0)
        * (temp_dry_c - temp_wet_c)
        * _PSYCH_A
        * (1.0 + _PSYCH_B * temp_wet_c)
    )
    return max(0.0, min(100.0, (e / es_dry) * 100.0))


def calculate_vpd(temp_dry_c: float, temp_wet_c: float, pressure_hpa: float = 1013.25) -> float:
    """Vapor pressure deficit (kPa) from a wet/dry bulb pair.

    Args:
        temp_dry_c: Dry-bulb temperature in °C.
        temp_wet_c: Wet-bulb temperature in °C.
        pressure_hpa: Ambient pressure in hectopascals (default = sea level).

    Returns:
        VPD in kPa, clamped at 0 (saturated). Returns 0.0 when
        ``temp_dry_c <= temp_wet_c``.
    """
    if temp_dry_c <= temp_wet_c:
        return 0.0
    es_dry = _saturation_kpa(temp_dry_c)
    es_wet = _saturation_kpa(temp_wet_c)
    e = es_wet - (
        (pressure_hpa / 1000.0)
        * (temp_dry_c - temp_wet_c)
        * _PSYCH_A
        * (1.0 + _PSYCH_B * temp_wet_c)
    )
    return max(0.0, es_dry - e)


def calculate_rh_from_dewpoint(temp_c: float, dewpoint_c: float) -> float:
    """Relative humidity (%) from a temperature/dewpoint pair.

    Used by the weather-service METAR pipeline (where the upstream gives
    temp and dewpoint, not wet bulb). Magnus formula, identical to what
    ``WeatherClient._calculate_rh`` did inline.

    Returns RH clamped to [0, 100].
    """
    es_t = _saturation_hpa(temp_c)
    es_d = _saturation_hpa(dewpoint_c)
    return max(0.0, min(100.0, (es_d / es_t) * 100.0))


__all__ = [
    "calculate_rh",
    "calculate_vpd",
    "calculate_rh_from_dewpoint",
]
