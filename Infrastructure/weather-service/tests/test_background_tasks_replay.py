from __future__ import annotations

from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from app.background_tasks import BackgroundTasks


class WeatherReplayTests(IsolatedAsyncioTestCase):
    async def test_failed_store_is_replayed_before_new_weather_data(self) -> None:
        first_report = {"temperature": 12.0, "timestamp": datetime(2026, 10, 20, 12, 0)}
        second_report = {"temperature": 13.0, "timestamp": datetime(2026, 10, 20, 12, 15)}
        database = MagicMock()
        database.store_weather_measurements = AsyncMock(
            side_effect=[TimeoutError("temporary database failure"), True, True]
        )
        weather_client = MagicMock()
        weather_client.fetch_metar = AsyncMock(side_effect=[first_report, second_report])
        tasks = BackgroundTasks({}, database, weather_client)
        tasks.sensor_ids = {"outside_temp": 1}

        await tasks._poll_weather()
        self.assertEqual(len(tasks._pending), 1)

        await tasks._poll_weather()

        stored_reports = [
            call.args[1] for call in database.store_weather_measurements.await_args_list
        ]
        self.assertEqual(stored_reports, [first_report, first_report, second_report])
        self.assertEqual(len(tasks._pending), 0)

    async def test_permanent_store_errors_drop_report_and_replay_following_rows(self) -> None:
        current_report = {
            "temperature": 10.0,
            "timestamp": datetime(2026, 10, 20, 12, 0),
        }
        rejected_pending = {
            "temperature": 11.0,
            "timestamp": datetime(2026, 10, 20, 12, 5),
        }
        later_pending = {
            "temperature": 12.0,
            "timestamp": datetime(2026, 10, 20, 12, 10),
        }
        database = MagicMock()
        database.store_weather_measurements = AsyncMock(
            side_effect=[
                RuntimeError("constraint violation"),
                RuntimeError("constraint violation"),
                True,
            ]
        )
        weather_client = MagicMock()
        weather_client.fetch_metar = AsyncMock(side_effect=[current_report, None])
        tasks = BackgroundTasks({}, database, weather_client)
        tasks.sensor_ids = {"outside_temp": 1}

        await tasks._poll_weather()
        self.assertEqual(len(tasks._pending), 0)

        tasks._pending.offer([rejected_pending, later_pending])
        await tasks._poll_weather()

        stored_reports = [
            call.args[1] for call in database.store_weather_measurements.await_args_list
        ]
        self.assertEqual(stored_reports, [current_report, rejected_pending, later_pending])
        self.assertEqual(len(tasks._pending), 0)
