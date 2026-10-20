from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, call, patch

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVICE_ROOT))

from app import main  # noqa: E402
from app.weather_client import WeatherClient  # noqa: E402


WEATHER_SETTINGS = {
    "station_icao": "CYQB",
    "poll_interval_seconds": 900,
    "api_url": "https://aviationweather.gov/api/data/metar",
    "room_name": "Outside",
    "device_name": "Weather Station Québec",
}


def test_weather_settings_require_a_complete_db_backed_snapshot() -> None:
    assert not main._has_usable_weather_settings({})
    assert not main._has_usable_weather_settings(
        {key: value for key, value in WEATHER_SETTINGS.items() if key != "device_name"}
    )
    assert main._has_usable_weather_settings(WEATHER_SETTINGS)


def test_weather_refresh_applies_complete_settings_snapshot() -> None:
    original = main.settings
    current = dict(WEATHER_SETTINGS)
    updated = {**WEATHER_SETTINGS, "station_icao": "CYUL", "poll_interval_seconds": 600}
    main.settings = current
    try:
        main._apply_weather_settings(updated)
        assert current == updated

        main._apply_weather_settings({"station_icao": "CYQB"})
        assert current == updated
    finally:
        main.settings = original


def test_weather_settings_loader_retries_incomplete_snapshots() -> None:
    async def run() -> None:
        pool = object()

        class Database:
            async def _get_pool(self) -> object:
                return pool

        with (
            patch(
                "app.main.ServiceSettings.load",
                new=AsyncMock(side_effect=[{}, WEATHER_SETTINGS]),
            ) as load,
            patch("app.main.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            loaded_pool, settings = await main._load_weather_settings(Database())

        assert loaded_pool is pool
        assert settings == WEATHER_SETTINGS
        assert load.await_args_list == [call(pool, "weather"), call(pool, "weather")]
        sleep.assert_awaited_once_with(10.0)

    asyncio.run(run())


def test_weather_client_uses_updated_database_station_and_api_url() -> None:
    client = WeatherClient(
        api_url="https://aviationweather.gov/api/data/metar", station_icao="CYQB"
    )
    http_client = client.client

    client.update_settings(api_url="https://weather.example/metar", station_icao="CYUL")

    assert client.api_url == "https://weather.example/metar"
    assert client.station_icao == "CYUL"
    assert client.client is http_client
    asyncio.run(client.close())
