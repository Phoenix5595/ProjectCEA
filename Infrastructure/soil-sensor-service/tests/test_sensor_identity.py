from __future__ import annotations

from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from app.background_tasks import BackgroundTasks, WiredProbe
from app.database import ABSENCE
from app.modbus_rtu import ModbusReply, ModbusStatus


def ok_reply(temperature_tenths: int) -> ModbusReply:
    return ModbusReply(ModbusStatus.OK, (325, temperature_tenths, 1100, 64))


NO_RESPONSE = ModbusReply(ModbusStatus.NO_RESPONSE)


class SoilPollingTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.database.observe_known_probe = AsyncMock(
            return_value={
                "registry_id": 8,
                "hardware_address": 233,
                "device_id": 42,
                "bed_name": None,
                "room_name": None,
            }
        )
        self.database.adopt_probe = AsyncMock()
        sensor_ids = {"temperature": 101, "humidity": 102, "ec": 103, "ph": 104}
        self.new_sensor_names = {
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
            return_value=(8, 42, sensor_ids, self.new_sensor_names)
        )
        self.database.store_measurements = AsyncMock(
            return_value=(8, sensor_ids, self.new_sensor_names)
        )
        self.redis = MagicMock()
        self.redis.write_to_stream = AsyncMock(return_value=True)
        self.redis.publish_all_readings = AsyncMock(return_value=True)
        self.tasks = BackgroundTasks(MagicMock(), self.database, self.redis)
        self.bus = MagicMock()
        self.bus.read_probe = AsyncMock(return_value=ok_reply(215))
        self.tasks.bus = self.bus
        self.tasks.probes[233] = WiredProbe(
            registry_id=4,
            sensor_names=self.cached_sensor_names,
            bed_name="Front Bed",
            room_name="Flower Room",
        )

    async def test_storage_uses_entry_returned_by_observe_known_probe(self) -> None:
        await self.tasks._poll_all_sensors()

        self.bus.read_probe.assert_awaited_once_with(233)
        self.database.observe_known_probe.assert_awaited_once_with(233)
        self.database.adopt_probe.assert_not_awaited()
        self.database.register_sensor_device.assert_awaited_once_with(8, 233, "soil_sensor_233")
        registry_id, address, name, readings, _timestamp = (
            self.database.store_measurements.await_args.args
        )
        self.assertEqual((registry_id, address, name), (8, 233, "soil_sensor_233"))
        self.assertEqual(readings, {"humidity": 32.5, "temperature": 21.5, "ec": 1100.0, "ph": 6.4})
        # The entry moved the probe to Unassigned: publication follows it.
        self.redis.publish_all_readings.assert_awaited_once_with(
            "soil_sensor_233",
            readings,
            None,
            "Flower Room",
            sensor_names=self.new_sensor_names,
        )
        self.assertEqual(self.tasks.probes[233].registry_id, 8)

    async def test_unknown_answering_address_is_adopted(self) -> None:
        self.database.observe_known_probe.return_value = None
        self.database.adopt_probe.return_value = {
            "registry_id": 4,
            "hardware_address": 233,
            "device_id": 42,
            "bed_name": None,
            "room_name": "Veg Room",
        }

        await self.tasks._poll_all_sensors()

        self.database.adopt_probe.assert_awaited_once_with(233)
        self.database.register_sensor_device.assert_not_awaited()
        self.assertEqual(self.redis.publish_all_readings.await_args.args[3], "Veg Room")

    async def test_failed_database_store_is_replayed_before_new_readings(self) -> None:
        successful_store = self.database.store_measurements.return_value
        self.database.store_measurements.side_effect = [
            TimeoutError("temporary database failure"),
            successful_store,
            successful_store,
        ]
        self.bus.read_probe.side_effect = [ok_reply(200), ok_reply(210)]

        await self.tasks._poll_all_sensors()
        self.assertEqual(len(self.tasks._pending_readings), 1)
        self.assertEqual(self.tasks._pending_readings.peek(1)[0][2], 8)

        await self.tasks._poll_all_sensors()

        stored_temperatures = [
            call.args[3]["temperature"] for call in self.database.store_measurements.await_args_list
        ]
        self.assertEqual(stored_temperatures, [20.0, 20.0, 21.0])
        self.assertEqual(len(self.tasks._pending_readings), 0)

    async def test_replay_uses_captured_registry_id_without_observing_again(self) -> None:
        self.bus.read_probe.return_value = NO_RESPONSE
        timestamp = datetime(2026, 10, 20, 12, 0)
        readings = {"temperature": 18.0}
        self.tasks._pending_readings.offer([("soil_sensor_233", 233, 8, readings, timestamp)])

        await self.tasks._poll_all_sensors()

        self.database.observe_known_probe.assert_not_awaited()
        self.database.store_measurements.assert_awaited_once_with(
            8, 233, "soil_sensor_233", readings, timestamp
        )

    async def test_permanent_database_errors_drop_rows_and_continue_replay(self) -> None:
        successful_store = self.database.store_measurements.return_value
        self.database.store_measurements.side_effect = [
            RuntimeError("constraint violation"),
            RuntimeError("constraint violation"),
            successful_store,
        ]
        self.bus.read_probe.side_effect = [ok_reply(190), NO_RESPONSE]

        await self.tasks._poll_all_sensors()
        self.assertEqual(len(self.tasks._pending_readings), 0)

        timestamp = datetime.now()
        self.tasks._pending_readings.offer(
            [
                ("soil_sensor_233", 233, 8, {"temperature": 18.0}, timestamp),
                ("soil_sensor_233", 233, 8, {"temperature": 17.0}, timestamp),
            ]
        )
        await self.tasks._poll_all_sensors()

        stored_temperatures = [
            call.args[3]["temperature"] for call in self.database.store_measurements.await_args_list
        ]
        self.assertEqual(stored_temperatures, [19.0, 18.0, 17.0])
        self.assertEqual(len(self.tasks._pending_readings), 0)

    async def test_failed_read_refreshes_nothing(self) -> None:
        self.bus.read_probe.return_value = NO_RESPONSE

        await self.tasks._poll_all_sensors()

        self.database.observe_known_probe.assert_not_awaited()
        self.database.store_measurements.assert_not_awaited()
        self.redis.publish_all_readings.assert_not_awaited()

    async def test_absence_threshold_is_ten_minutes(self) -> None:
        self.assertEqual(ABSENCE, timedelta(minutes=10))
