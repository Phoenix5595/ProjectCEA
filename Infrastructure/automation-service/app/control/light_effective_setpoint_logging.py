"""Per-light effective intensity logging to DB for Grafana (extracted from ControlEngine loop)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)


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
) -> None:
    """Throttle and write per-dimmer light effective intensity rows (SUN/MOON) for dashboards."""
    if not scheduler:
        return
    try:
        for device_name, device_info in cluster_devices.items():
            if device_info.get("device_type") != "light":
                continue
            if (
                not device_info.get("dimming_enabled")
                or device_info.get("dimming_type") != "dfr0971"
            ):
                continue

            key = (location, cluster, device_name)
            last_ts = last_light_effective_log.get(key)
            if last_ts and (current_time - last_ts).total_seconds() < interval_sec:
                continue

            det = scheduler.get_light_intensity_details(
                location, cluster, device_name, current_time
            )
            if det is None:
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
        logger.debug(f"Light effective_setpoints logging skipped: {e}")
