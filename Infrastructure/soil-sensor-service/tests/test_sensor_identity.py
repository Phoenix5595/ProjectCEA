from __future__ import annotations

from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from app.background_tasks import BackgroundTasks
from app.database import ABSENCE


class SoilIdentityPollingTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.database.observe_probe = AsyncMock(
            return_value={
                "registry_id": 8,
                "hardware_address": 233,
                "rack_id": None,
                "bed_name": None,
                "sensor_uid": "00000000-0000-4000-8000-000000000008",
                "device_id": 42,
                "identity_status": "pending",
                "pending_candidate_registry_id": 4,
                "pending_since": None,
                "last_seen": None,
            }
        )
        sensor_ids = {"temperature": 101, "humidity": 102, "ec": 103, "ph": 104}
        sensor_names = {
            "temperature": "soil_sensor_226_temperature",
            "humidity": "soil_sensor_226_humidity",
            "ec": "soil_sensor_226_ec",
            "ph": "soil_sensor_226_ph",
        }
        self.cached_sensor_names = {
            "temperature": "soil_sensor_233_temperature",
            "humidity": "soil_sensor_233_humidity",
            "ec": "soil_sensor_233_ec",
            "ph": "soil_sensor_233_ph",
        }
        self.database.register_sensor_device = AsyncMock(
            return_value=(8, 42, sensor_ids, sensor_names)
        )
        self.database.store_measurements = AsyncMock(return_value=(8, sensor_ids, sensor_names))
        self.redis = MagicMock()
        self.redis.write_to_stream = AsyncMock(return_value=True)
        self.redis.publish_all_readings = AsyncMock(return_value=True)
        self.tasks = BackgroundTasks(MagicMock(), self.database, self.redis)
        self.tasks.sensor_names["soil_sensor_233"] = self.cached_sensor_names
        self.reader = MagicMock()
        self.reader.read_all_parameters.return_value = {"temperature": 21.5}
        self.tasks.sensor_readers["soil_sensor_233"] = self.reader
        self.tasks.sensor_configs["soil_sensor_233"] = {
            "modbus_id": 233,
            "bed_name": "Front Bed",
        }
        self.tasks.registry_ids[233] = 4

    async def test_read_switches_to_observed_registry_device_before_storage(self) -> None:
        await self.tasks._poll_all_sensors()

        self.database.observe_probe.assert_awaited_once_with(233)
        self.database.register_sensor_device.assert_awaited_once_with(8, 233, "soil_sensor_233")
        self.database.store_measurements.assert_awaited_once()
        registry_id, modbus_id, probe_name, readings, _timestamp = (
            self.database.store_measurements.await_args.args
        )
        self.assertEqual((registry_id, modbus_id, probe_name), (8, 233, "soil_sensor_233"))
        self.assertEqual(readings, {"temperature": 21.5})
        self.redis.publish_all_readings.assert_awaited_once_with(
            "soil_sensor_233",
            {"temperature": 21.5},
            "Front Bed",
            "Flower Room",
            sensor_names=self.cached_sensor_names,
        )
        self.assertEqual(
            self.tasks.sensor_names["soil_sensor_233"],
            self.database.register_sensor_device.return_value[3],
        )
        self.assertEqual(self.tasks.registry_ids[233], 8)
        self.assertIsNone(self.tasks.sensor_configs["soil_sensor_233"]["bed_name"])

    async def test_failed_database_store_is_replayed_before_new_readings(self) -> None:
        successful_store = self.database.store_measurements.return_value
        self.database.store_measurements.side_effect = [
            TimeoutError("temporary database failure"),
            successful_store,
            successful_store,
        ]
        self.reader.read_all_parameters.side_effect = [
            {"temperature": 20.0},
            {"temperature": 21.0},
        ]

        await self.tasks._poll_all_sensors()
        self.assertEqual(len(self.tasks._pending_readings), 1)

        await self.tasks._poll_all_sensors()

        stored_temperatures = [
            call.args[3]["temperature"] for call in self.database.store_measurements.await_args_list
        ]
        self.assertEqual(stored_temperatures, [20.0, 20.0, 21.0])
        self.assertEqual(len(self.tasks._pending_readings), 0)

    async def test_permanent_database_errors_drop_rows_and_continue_replay(self) -> None:
        successful_store = self.database.store_measurements.return_value
        self.database.store_measurements.side_effect = [
            RuntimeError("constraint violation"),
            RuntimeError("constraint violation"),
            successful_store,
        ]
        self.reader.read_all_parameters.side_effect = [{"temperature": 19.0}, None]

        await self.tasks._poll_all_sensors()
        self.assertEqual(len(self.tasks._pending_readings), 0)

        timestamp = datetime.now()
        self.tasks._pending_readings.offer(
            [
                ("soil_sensor_233", 233, {"temperature": 18.0}, timestamp),
                ("soil_sensor_233", 233, {"temperature": 17.0}, timestamp),
            ]
        )
        await self.tasks._poll_all_sensors()

        stored_temperatures = [
            call.args[3]["temperature"] for call in self.database.store_measurements.await_args_list
        ]
        self.assertEqual(stored_temperatures, [19.0, 18.0, 17.0])
        self.assertEqual(len(self.tasks._pending_readings), 0)

    async def test_failed_read_does_not_refresh_identity_freshness(self) -> None:
        self.reader.read_all_parameters.return_value = None

        await self.tasks._poll_all_sensors()

        self.database.observe_probe.assert_not_awaited()
        self.database.store_measurements.assert_not_awaited()

    async def test_absence_threshold_is_ten_minutes(self) -> None:
        self.assertEqual(ABSENCE, timedelta(minutes=10))
