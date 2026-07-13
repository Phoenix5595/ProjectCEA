"""Photoperiod calculation (sun/moon, day_start/night_start)."""

from __future__ import annotations

from datetime import datetime

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class PhotoperiodMixin:
    """Mixin for photoperiod (lights-on window) calculations."""

    def is_in_photoperiod(self, location: str, cluster: str, current_time: datetime) -> bool:
        """True if current time is in the room's sun (lights-on) window.

        Reads from ``self._mode_params[(location, cluster)]`` cache.
        The photoperiod is ``[day_start, night_start)``.

        Handles overnight wrap: if ``day_start > night_start``, the photoperiod
        spans midnight (e.g., day_start=17:00, night_start=11:00 means lights on
        from 17:00 to 11:00 next day).

        **Failsafe:** If no mode_params exist for ``(location, cluster)``, returns
        ``True`` so lights go to 10% + relay ON (NOT darkness). The CRITICAL alarm
        fires from T9 (AlarmManager).

        Args:
            location: Room name
            cluster: Cluster name
            current_time: Current time

        Returns:
            True if in photoperiod (or failsafe when mode_params missing).
        """
        params = self._mode_params.get((location, cluster))
        if params is None:
            # Failsafe: treat as in-photoperiod so lights go to 10% + relay ON
            logger.warning(
                f"is_in_photoperiod: no mode_params for {location}/{cluster} "
                f"- returning True (failsafe: 10% + relay ON, NOT darkness)"
            )
            return True

        day_start = self._parse_time(params.get("day_start"))
        night_start = self._parse_time(params.get("night_start"))

        if not day_start or not night_start:
            logger.warning(
                f"is_in_photoperiod: invalid day_start/night_start for "
                f"{location}/{cluster} - returning True (failsafe)"
            )
            return True

        current_time_obj = current_time.time()

        if day_start > night_start:
            # Overnight photoperiod: e.g., 17:00 to 11:00 next day
            # In photoperiod if current >= day_start OR current < night_start
            return current_time_obj >= day_start or current_time_obj < night_start
        else:
            # Normal photoperiod: e.g., 06:00 to 18:00
            # In photoperiod if day_start <= current < night_start
            return day_start <= current_time_obj < night_start
