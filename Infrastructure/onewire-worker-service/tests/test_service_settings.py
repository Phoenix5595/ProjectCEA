from __future__ import annotations

from pathlib import Path
import sys

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVICE_ROOT))

from app.config import ConfigLoader  # noqa: E402


def test_yaml_interval_fallback_is_overridden_without_changing_device_mapping(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "onewire_config.yaml"
    config_path.write_text(
        "devices:\n  28-test: lab_temp\npolling:\n  interval_seconds: 3\n",
        encoding="utf-8",
    )
    config = ConfigLoader(str(config_path))

    assert config.get_service_settings_fallback() == {"poll_interval_seconds": 3}
    assert config.get_polling_interval_seconds() == 3.0
    assert config.get_devices() == {"28-test": "lab_temp"}

    config.apply_service_settings({"poll_interval_seconds": 9})

    assert config.get_polling_interval_seconds() == 9.0
    assert config.get_devices() == {"28-test": "lab_temp"}
