"""Read 1-Wire DS18B20 temperature from sysfs."""

from __future__ import annotations

from pathlib import Path

from shared.logging import get_logger

logger = get_logger(__name__)

W1_DEVICES = Path("/sys/bus/w1/devices")


def read_temperature_c(device_id: str) -> float | None:
    """Read temperature in Celsius for a 28-* device. Returns None on error."""
    path = W1_DEVICES / device_id / "temperature"
    if not path.exists() or not path.is_file():
        return None
    try:
        raw = path.read_text().strip()
        millideg = int(raw)
        return round(millideg / 1000.0, 2)
    except (ValueError, OSError) as e:
        logger.debug(f"Read failed for {device_id}: {e}")
        return None
