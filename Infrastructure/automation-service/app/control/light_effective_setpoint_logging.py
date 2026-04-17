"""Per-light effective intensity logging to DB for Grafana (extracted from ControlEngine loop)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Throttle repeated warnings (per device or cluster)
_last_det_none_warn: dict[tuple[str, str, str], float] = {}
_last_per_device_log_error: dict[tuple[str, str, str], float] = {}


def _has_schedule_rows_for_device(
    scheduler: Scheduler, location: str, cluster: str, device_name: str
) -> bool:
    for s in scheduler.schedules:
        if (
            s.get("location") == location
            and s.get("cluster") == cluster
            and s.get("device_name") == device_name
        ):
            return True
    return False


async def log_light_effective_intensities_for_cluster(
    *,
    location: str,
    cluster: str,
    cluster_devices: dict[str, dict[str, Any]],
    current_time: datetime,
    is_sun: bool,
    scheduler: Scheduler | None,
    database: DatabaseManager,
    last_light_effective_log: dict[tuple[str, str, str], datetime],
    interval_sec: float = 60.0,
    redis_client: Any | None = None,
    last_sun_schedule_gap_error: dict[tuple[str, str, str], float] | None = None,
    sun_schedule_gap_error_interval_sec: float = 60.0,
) -> None:
    """Throttle and write per-dimmer light effective intensity rows (SUN/MOON) for dashboards."""
    if not scheduler:
        return

    for device_name, device_info in cluster_devices.items():
        if device_info.get("device_type") != "light":
            continue
        if not device_info.get("dimming_enabled") or device_info.get("dimming_type") != "dfr0971":
            continue

        key = (location, cluster, device_name)
        try:
            last_ts = last_light_effective_log.get(key)
            if last_ts and (current_time - last_ts).total_seconds() < interval_sec:
                continue

            current_intensity: float | None = None
            if redis_client and getattr(redis_client, "redis_enabled", False):
                try:
                    data = redis_client.read_light_intensity(location, cluster, device_name)
                    if data and data.get("intensity") is not None:
                        current_intensity = float(data["intensity"])
                except (TypeError, ValueError):
                    current_intensity = None

            det = scheduler.get_light_intensity_details(
                location, cluster, device_name, current_time, current_intensity
            )
            if det is None:
                now_ts = current_time.timestamp()
                if is_sun:
                    gap_key = (location, cluster, device_name)
                    last_err = (
                        last_sun_schedule_gap_error.get(gap_key, 0.0)
                        if last_sun_schedule_gap_error is not None
                        else 0.0
                    )
                    if now_ts - last_err >= sun_schedule_gap_error_interval_sec:
                        if last_sun_schedule_gap_error is not None:
                            last_sun_schedule_gap_error[gap_key] = now_ts
                        logger.error(
                            "Light intensity details missing during photoperiod (SUN): "
                            "location=%s cluster=%s device=%s — check enabled SUN/DAY and "
                            "MOON/NIGHT schedule rows with valid times in ZoneConfig.",
                            location,
                            cluster,
                            device_name,
                        )
                if now_ts - _last_det_none_warn.get(key, 0.0) >= 60.0:
                    _last_det_none_warn[key] = now_ts
                    has_rows = _has_schedule_rows_for_device(
                        scheduler, location, cluster, device_name
                    )
                    logger.warning(
                        "get_light_intensity_details returned None: location=%s cluster=%s "
                        "device=%s is_sun=%s schedule_rows_for_device=%s",
                        location,
                        cluster,
                        device_name,
                        is_sun,
                        has_rows,
                    )
                continue

            await database.setpoint_repo.log_effective_setpoints(
                location=location,
                cluster=cluster,
                device_name=device_name,
                mode="SUN" if is_sun else "MOON",
                effective_light_intensity=float(det["effective_intensity"]),
                nominal_light_intensity=float(det["nominal_intensity"]),
                ramp_progress_light=det.get("ramp_progress"),
                timestamp=current_time,
            )
            last_light_effective_log[key] = current_time
        except Exception as e:
            now_ts = current_time.timestamp()
            if now_ts - _last_per_device_log_error.get(key, 0.0) >= 60.0:
                _last_per_device_log_error[key] = now_ts
                logger.warning(
                    "Light effective_setpoints logging failed for %s/%s/%s: %s",
                    location,
                    cluster,
                    device_name,
                    e,
                    exc_info=True,
                )
