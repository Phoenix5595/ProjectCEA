from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any

import pytest

import shared.service_settings as service_settings
from shared.service_settings import (
    ServiceSettings,
    SettingValidationError,
    validate_settings,
    write_cache,
)


class FakePool:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        updated_at: list[datetime | None] | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.updated_at = list(updated_at or [])
        self.fetch_error = fetch_error

    async def fetch(self, query: str, service: str) -> list[dict[str, Any]]:
        assert "service_setting" in query
        assert service
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.rows

    async def fetchval(self, query: str, service: str) -> datetime | None:
        assert "max(updated_at)" in query
        assert service
        return self.updated_at.pop(0)


def test_load_prefers_database_then_cache_then_fallback(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache" / "soil.json"
    write_cache(
        cache_path,
        {"poll_interval_seconds": 20, "discovery_interval_seconds": 60},
    )
    pool = FakePool(
        rows=[{"key": "poll_interval_seconds", "value": "10"}],
    )

    loaded = asyncio.run(
        ServiceSettings.load(
            pool,
            "soil",
            cache_path=cache_path,
            fallback={"poll_interval_seconds": 5, "discovery_interval_seconds": 30},
        )
    )

    assert loaded == {"poll_interval_seconds": 10, "discovery_interval_seconds": 60}
    assert json.loads(cache_path.read_text(encoding="utf-8"))["poll_interval_seconds"] == 10


def test_load_uses_cache_then_fallback_when_database_is_unavailable(tmp_path: Path) -> None:
    cache_path = tmp_path / "soil.json"
    write_cache(cache_path, {"poll_interval_seconds": 20})
    pool = FakePool(fetch_error=ConnectionError("database unavailable"))

    loaded = asyncio.run(
        ServiceSettings.load(
            pool,
            "soil",
            cache_path=cache_path,
            fallback={"poll_interval_seconds": 5, "discovery_interval_seconds": 30},
        )
    )

    assert loaded == {"poll_interval_seconds": 20, "discovery_interval_seconds": 30}


def test_run_refresh_calls_callback_only_for_changed_timestamp(tmp_path: Path) -> None:
    first = datetime(2026, 10, 19, tzinfo=UTC)
    changed = first + timedelta(seconds=1)
    pool = FakePool(
        rows=[{"key": "poll_interval_seconds", "value": "10"}],
        updated_at=[first, first, changed],
    )
    callbacks: list[dict[str, Any]] = []

    class StopRefresh(Exception):
        pass

    def on_change(values: dict[str, Any]) -> None:
        callbacks.append(values)
        raise StopRefresh

    with pytest.raises(StopRefresh):
        asyncio.run(
            ServiceSettings.run_refresh(
                pool,
                "soil",
                on_change,
                interval_s=0,
                cache_path=tmp_path / "soil.json",
            )
        )

    assert callbacks == [{"poll_interval_seconds": 10}]


@pytest.mark.parametrize(
    ("service", "key", "value", "current"),
    [
        ("automation", "update_interval", 0, None),
        ("automation", "update_interval", True, None),
        ("automation", "last_good_hold_period", 3601, None),
        ("automation", "binary_hysteresis", 10.1, None),
        ("soil", "poll_interval_seconds", 301, None),
        ("soil", "discovery_interval_seconds", 4, None),
        ("onewire", "poll_interval_seconds", 0, None),
        ("weather", "station_icao", "cyqb", None),
        ("weather", "poll_interval_seconds", 59, None),
        ("weather", "api_url", "http://weather.example", None),
        (
            "automation",
            "pid_defaults",
            {"heater": {"kp": 101, "ki": 0.02, "kd": 0}},
            {
                "pid_limits": {
                    "heater": {
                        "kp_min": 0,
                        "kp_max": 100,
                        "ki_min": 0,
                        "ki_max": 1,
                        "kd_min": 0,
                        "kd_max": 10,
                    }
                }
            },
        ),
        (
            "automation",
            "safety_limits",
            {
                "min_temperature": 35,
                "max_temperature": 35,
                "min_humidity": 30,
                "max_humidity": 90,
                "min_co2": 400,
                "max_co2": 2000,
            },
            None,
        ),
    ],
)
def test_validation_errors_identify_the_setting(
    service: str, key: str, value: Any, current: dict[str, Any] | None
) -> None:
    with pytest.raises(SettingValidationError) as error:
        validate_settings(service, {key: value}, current=current)

    assert error.value.key == key
    assert error.value.code == "setting_invalid"


def test_pid_limits_reuse_automation_config_schema() -> None:
    with pytest.raises(SettingValidationError) as error:
        validate_settings(
            "automation",
            {
                "pid_limits": {
                    "heater": {
                        "kp_min": -1,
                        "kp_max": 100,
                        "ki_min": 0,
                        "ki_max": 1,
                        "kd_min": 0,
                        "kd_max": 10,
                    }
                }
            },
        )

    assert error.value.key == "pid_limits"


@pytest.mark.parametrize("key", ["room_name", "device_name"])
def test_weather_room_and_device_are_read_only(key: str) -> None:
    with pytest.raises(SettingValidationError) as error:
        validate_settings("weather", {key: "New value"})

    assert error.value.key == key
    assert error.value.code == "setting_read_only"
    assert error.value.reason == "setting_read_only"


def test_cache_write_uses_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_path = tmp_path / "nested" / "settings.json"
    real_replace = service_settings.os.replace
    replacements: list[tuple[Path, Path]] = []

    def observe_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == destination_path.parent
        assert source_path.is_file()
        replacements.append((source_path, destination_path))
        real_replace(source_path, destination_path)

    monkeypatch.setattr(service_settings.os, "replace", observe_replace)
    write_cache(cache_path, {"station_icao": "CYQB"})

    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"station_icao": "CYQB"}
    assert len(replacements) == 1
    assert replacements[0][1] == cache_path
    assert not replacements[0][0].exists()
