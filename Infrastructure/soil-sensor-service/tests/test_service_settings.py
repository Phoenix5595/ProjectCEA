from __future__ import annotations

from pathlib import Path
import sys

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVICE_ROOT))

from app.config import ConfigLoader  # noqa: E402


def test_yaml_interval_fallbacks_are_overridden_by_service_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "soil_sensor_config.yaml"
    config_path.write_text(
        "polling:\n  interval_seconds: 7\n  discovery_interval_seconds: 45\n",
        encoding="utf-8",
    )
    config = ConfigLoader(str(config_path))

    assert config.get_service_settings_fallback() == {
        "poll_interval_seconds": 7,
        "discovery_interval_seconds": 45,
    }
    assert config.get_polling_config() == {
        "interval_seconds": 7,
        "discovery_interval_seconds": 45,
    }

    config.apply_service_settings(
        {"poll_interval_seconds": 12, "discovery_interval_seconds": 60}
    )

    assert config.get_polling_config() == {
        "interval_seconds": 12,
        "discovery_interval_seconds": 60,
    }
