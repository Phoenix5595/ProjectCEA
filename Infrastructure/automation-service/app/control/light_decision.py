"""Canonical light decision and authority resolution utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class LightDecision:
    """Single source-of-truth decision for one light in one control loop."""

    location: str
    cluster: str
    device_name: str
    authority: str
    is_sun: bool
    effective_percent: float
    nominal_percent: float | None
    ramp_progress: float | None
    reason: str
    decision_time: datetime


class LightAuthorityResolver:
    """Resolve light authority with strict priority.

    Priority:
    1. safety interlock
    2. manual override (optionally TTL-bound)
    3. schedule automation
    """

    def resolve(
        self,
        *,
        current_time: datetime,
        device_info: dict[str, Any],
        is_sun: bool,
        scheduled_percent: float,
        nominal_percent: float | None,
        ramp_progress: float | None,
        failsafe_active: bool = False,
    ) -> LightDecision:
        location = str(device_info.get("_location") or "")
        cluster = str(device_info.get("_cluster") or "")
        device_name = str(device_info.get("_device_name") or "")

        if failsafe_active:
            return LightDecision(
                location=location,
                cluster=cluster,
                device_name=device_name,
                authority="safety",
                is_sun=False,
                effective_percent=0.0,
                nominal_percent=nominal_percent,
                ramp_progress=None,
                reason="failsafe active",
                decision_time=current_time,
            )

        override_percent = device_info.get("manual_override_percent")
        override_until = device_info.get("manual_override_until")
        override_ttl_s = device_info.get("manual_override_ttl_seconds")
        override_active = False

        if override_percent is not None:
            if isinstance(override_until, datetime):
                override_active = current_time <= override_until
            elif isinstance(override_ttl_s, (int, float)) and float(override_ttl_s) > 0:
                start = device_info.get("manual_override_started_at")
                if isinstance(start, datetime):
                    override_active = current_time <= (
                        start + timedelta(seconds=float(override_ttl_s))
                    )
                else:
                    override_active = True
            else:
                override_active = True

        if override_active:
            pct = max(0.0, min(100.0, float(override_percent)))
            return LightDecision(
                location=location,
                cluster=cluster,
                device_name=device_name,
                authority="manual_override",
                is_sun=pct > 0.0,
                effective_percent=pct,
                nominal_percent=pct,
                ramp_progress=None,
                reason="manual override",
                decision_time=current_time,
            )

        return LightDecision(
            location=location,
            cluster=cluster,
            device_name=device_name,
            authority="schedule",
            is_sun=is_sun,
            effective_percent=max(0.0, min(100.0, scheduled_percent)),
            nominal_percent=nominal_percent,
            ramp_progress=ramp_progress,
            reason="schedule automation",
            decision_time=current_time,
        )
