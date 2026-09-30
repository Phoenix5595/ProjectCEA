"""DB-authoritative historical sensor route behavior tests (step 4, #3).

In-process route calls with a fake repository — no ASGI server, no real
database, no Redis stream construction. Also pins the pure aggregate-tier
thresholds that stay unchanged.
"""

from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from app.models import DataPoint, SensorDataResponse
from app.repositories.sensor_repository import _pick_aggregate_tier
from app.routes import sensors as sensors_mod


class FakeRequest:
    def __init__(self, params: dict[str, str]) -> None:
        self._params = params

    @property
    def query_params(self):
        return self._params


class FakeRepository:
    def __init__(self, points: dict[str, list[DataPoint]] | Exception) -> None:
        self._result = points
        self.calls: list[tuple[str, str, datetime, datetime]] = []

    async def get_sensor_data(self, location, cluster, start_time, end_time):
        self.calls.append((location, cluster, start_time, end_time))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeSentinelStreamReader:
    """Constructed => historical route touched the removed stream branch."""

    constructed = 0

    def __init__(self, *args, **kwargs) -> None:
        FakeSentinelStreamReader.constructed += 1
        raise AssertionError("historical route must not construct RedisStreamReader")


def db_points(start: datetime, end: datetime) -> dict[str, list[DataPoint]]:
    return {
        "dry_bulb_b": [
            DataPoint(timestamp=start, value=18.0, unit="°C"),
            DataPoint(timestamp=end, value=26.0, unit="°C"),
        ]
    }


class SensorHistoryAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeSentinelStreamReader.constructed = 0

    def _patches(self, repo: FakeRepository):
        from contextlib import ExitStack

        stack = ExitStack()
        stack.enter_context(patch.object(sensors_mod, "get_sensor_repository", return_value=repo))
        stack.enter_context(
            patch.object(sensors_mod, "RedisStreamReader", FakeSentinelStreamReader, create=True)
        )
        return stack

    async def test_recent_window_edges_come_from_db_independently_of_partial_stream(
        self,
    ) -> None:
        """A partial-stream fixture must be irrelevant: both edge points of
        a recent (<=6h) window come from the single DB call, and the stream
        reader sentinel is never constructed."""
        start = datetime(2026, 1, 1, 10, 0, 0)
        end = datetime(2026, 1, 1, 12, 0, 0)  # 2h window (recent)
        repo = FakeRepository(
            {
                "dry_bulb_b": [
                    DataPoint(timestamp=start, value=18.0, unit="°C"),
                    DataPoint(timestamp=start + (end - start) / 2, value=22.0, unit="°C"),
                    DataPoint(timestamp=end, value=26.0, unit="°C"),
                ]
            }
        )
        with self._patches(repo):
            response = await sensors_mod.get_sensor_data(
                location="Flower Room",
                cluster="back",
                request=FakeRequest({}),
                start_time=start,
                end_time=end,
                time_range="1 Hour",
            )

        self.assertEqual(
            FakeSentinelStreamReader.constructed,
            0,
            "stream reader must never be constructed by the historical route",
        )
        self.assertEqual(len(repo.calls), 1, "exactly one repository call")
        self.assertEqual(repo.calls[0][:2], ("Flower Room", "back"))
        self.assertEqual(repo.calls[0][2], start)
        self.assertEqual(repo.calls[0][3], end)
        values = [p.value for p in response["dry_bulb_b"].data]
        self.assertEqual(values, [18.0, 22.0, 26.0], "both edges present")
        self.assertEqual(response["dry_bulb_b"].unit, "°C")

    async def test_explicit_window_preserves_arguments_and_response_shape(self) -> None:
        start = datetime(2026, 3, 1, 8, 30, 0)
        end = datetime(2026, 3, 1, 14, 30, 0)
        repo = FakeRepository({"rh_b": [DataPoint(timestamp=start, value=55.0, unit="%")]})
        with self._patches(repo):
            response = await sensors_mod.get_sensor_data(
                location="Flower Room",
                cluster="front",
                request=FakeRequest({"start_time": start.isoformat(), "end_time": end.isoformat()}),
                start_time=start,
                end_time=end,
                time_range="6 Hours",
            )

        self.assertEqual(repo.calls, [("Flower Room", "front", start, end)])
        self.assertIsInstance(response["rh_b"], SensorDataResponse)
        self.assertEqual(response["rh_b"].cluster, "front")

    async def test_preset_window_resolves_defaults_with_one_db_call(self) -> None:
        repo = FakeRepository({})
        with self._patches(repo):
            await sensors_mod.get_sensor_data(
                location="Veg Room",
                cluster="main",
                request=FakeRequest({"time_range": "24 Hours"}),
                start_time=None,
                end_time=None,
                time_range="24 Hours",
            )

        self.assertEqual(len(repo.calls), 1)
        location, cluster, start, end = repo.calls[0]
        self.assertEqual((location, cluster), ("Veg Room", "main"))
        self.assertAlmostEqual((end - start).total_seconds(), 86400.0)

    async def test_fallback_datetime_query_string_is_parsed_unconditionally(self) -> None:
        """FastAPI-less query strings still resolve (parsing must not depend
        on debug level). With BOTH bounds from query strings, the resolved
        window preserves the parsed values."""
        repo = FakeRepository({})
        start_iso = "2026-05-01T06:00:00"
        end_iso = "2026-05-01T07:00:00"
        with self._patches(repo):
            await sensors_mod.get_sensor_data(
                location="Lab",
                cluster="main",
                request=FakeRequest({"start_time": start_iso, "end_time": end_iso}),
                start_time=None,
                end_time=None,
                time_range="1 Hour",
            )

        self.assertEqual(len(repo.calls), 1)
        self.assertEqual(repo.calls[0][2], datetime.fromisoformat(start_iso))
        self.assertEqual(repo.calls[0][3], datetime.fromisoformat(end_iso))

    async def test_invalid_topology_fails_before_any_io(self) -> None:
        repo = FakeRepository({"x": [DataPoint(timestamp=datetime(2026, 1, 1), value=1, unit="")]})

        class FailingReader:
            def __init__(self, *a, **k) -> None:
                raise AssertionError("no reader")

        with (
            patch.object(sensors_mod, "get_sensor_repository", return_value=repo),
            patch.object(sensors_mod, "RedisStreamReader", FailingReader, create=True),
            self.assertRaises(sensors_mod.ValidationAPIError) as denied,
        ):
            await sensors_mod.get_sensor_data(
                location="Flower Room",
                cluster="main",  # device cluster for Flower Room -> invalid
                request=FakeRequest({}),
                start_time=None,
                end_time=None,
                time_range="1 Hour",
            )
        self.assertEqual(denied.exception.status_code, 422)
        self.assertEqual(repo.calls, [], "no I/O before topology validation")

    async def test_empty_db_gives_empty_response(self) -> None:
        repo = FakeRepository({})
        with self._patches(repo):
            response = await sensors_mod.get_sensor_data(
                location="Veg Room",
                cluster="main",
                request=FakeRequest({}),
                start_time=datetime(2026, 1, 1),
                end_time=datetime(2026, 1, 1, 1),
                time_range="1 Hour",
            )
        self.assertEqual(response, {})

    async def test_db_error_propagates_no_silent_fallback(self) -> None:
        repo = FakeRepository(RuntimeError("db down"))
        with self._patches(repo), self.assertRaises(RuntimeError):
            await sensors_mod.get_sensor_data(
                location="Veg Room",
                cluster="main",
                request=FakeRequest({}),
                start_time=datetime(2026, 1, 1),
                end_time=datetime(2026, 1, 1, 1),
                time_range="1 Hour",
            )
        self.assertEqual(len(repo.calls), 1, "single DB attempt, error propagates")

    async def test_aggregate_tier_thresholds_unchanged(self) -> None:
        """Pure tier selection pins: 2h/24h/7d/30d ladder boundaries."""
        self.assertEqual(_pick_aggregate_tier(0.5).name, "raw")
        self.assertEqual(_pick_aggregate_tier(2.0).name, "1min")
        self.assertEqual(_pick_aggregate_tier(1.999).name, "raw")
        self.assertEqual(_pick_aggregate_tier(24.0).name, "5min")
        self.assertEqual(_pick_aggregate_tier(23.9).name, "1min")
        self.assertEqual(_pick_aggregate_tier(7 * 24.0).name, "hourly")
        self.assertEqual(_pick_aggregate_tier(30 * 24.0).name, "daily")
        self.assertEqual(_pick_aggregate_tier(29 * 24.0).name, "hourly")


if __name__ == "__main__":
    unittest.main()
