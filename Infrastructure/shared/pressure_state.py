"""Per-process in-memory tracker for the last observed BME280 pressure.

Used by ``can-processor-service`` so that when a BME280 frame arrives with
``pressure_hpa``, subsequent PT100 frames at the same (location, cluster)
get the actual measured pressure plugged into the wet-bulb RH/VPD math
instead of the 1013.25 hPa sea-level default.

Design notes
------------
- **In-memory, per-process.** Each service has its own ``_state`` dict.
  We do not push pressure into Redis — the wet-bulb correction term is
  small (sub-0.1 % RH at typical greenhouse pressure deltas) so the
  observability cost of an extra round-trip per PT100 frame is not worth
  it. The trade-off: a service restart momentarily reverts to 1013.25
  for the first second of operation until the next BME280 frame lands
  (BME280 publishes at 1 Hz on the live bus).
- **Lazy default.** Lookups for an unseen (location, cluster) return the
  sea-level fallback rather than raising — the math is well-defined for
  any positive pressure, and the alternative (raise) would require every
  caller to wrap in try/except.
- **No locking.** Python dict assignment is atomic under the GIL for
  these primitive value types and the writers/readers all live on the
  asyncio event loop in their respective services. If a future Phase 6
  step moves any caller to a thread pool, add an asyncio.Lock here.

Replaces the duplicate (and broken — see git log) ``update_pressure_state``
/ ``get_pressure_state`` shims that existed inline in
``can-processor-service/app/processor.py`` and
``backend/app/stream_processor.py``.
"""

from __future__ import annotations

from collections import defaultdict

# Sea-level standard pressure (hPa). Used as the lookup fallback before
# the first real BME280 reading lands.
SEA_LEVEL_HPA: float = 1013.25

_state: defaultdict[tuple[str, str], float] = defaultdict(lambda: SEA_LEVEL_HPA)


def update_pressure_state(location: str, cluster: str, pressure_hpa: float) -> None:
    """Record the latest observed pressure for ``(location, cluster)``."""
    _state[(location, cluster)] = float(pressure_hpa)


def get_pressure_state(location: str, cluster: str) -> float:
    """Return the last observed pressure for ``(location, cluster)``.

    Returns ``SEA_LEVEL_HPA`` if no BME280 frame has been seen for this
    combination yet in the current process.
    """
    return _state[(location, cluster)]


__all__ = [
    "SEA_LEVEL_HPA",
    "update_pressure_state",
    "get_pressure_state",
]
