"""SoilBus, ModbusRTU and ProbeDiscovery against a pty-backed fake SEN0604 bus."""

from __future__ import annotations

import os
import select
import struct
import threading
import time
import tty
from dataclasses import dataclass
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.discovery import ProbeDiscovery
from app.modbus_rtu import ModbusStatus, add_crc
from app.soil_bus import SoilBus
from app.soil_sensor_reader import decode_sen0604

WIKI_REGISTERS = (0x0292, 0xFF9B, 0x03E8, 0x0038)
WIKI_READ_REQUEST = bytes.fromhex("010300000004 4409")
WIKI_RESPONSE = bytes.fromhex("01030802 92FF9B03 E8003857 B6")
ADDRESS_WRITE_TO_3 = bytes.fromhex("010607D00003C946")


@dataclass
class FakeProbe:
    address: int
    registers: tuple[int, int, int, int] = WIKI_REGISTERS
    readdress_after_power_cycle: bool = False
    pending_address: int | None = None

    def reply(self, frame: bytes) -> bytes | None:
        slave, function, register, value = struct.unpack(">BBHH", frame[:6])
        if slave != self.address:
            return None
        if function == 0x03 and register == 0x0000 and value == 4:
            return add_crc(struct.pack(">BBB4H", slave, 0x03, 8, *self.registers))
        if function == 0x06 and register == 0x07D0:
            if self.readdress_after_power_cycle:
                self.pending_address = value
            else:
                self.address = value
            return frame
        return add_crc(struct.pack(">BBB", slave, function | 0x80, 0x02))


class FakeBus:
    """Emulates SEN0604 probes on the master side of a pty."""

    def __init__(self) -> None:
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        tty.setraw(self.master)
        self.port = os.ttyname(self.slave)
        self.probes: list[FakeProbe] = []
        self.noise_addresses: set[int] = set()
        self.requests: list[tuple[float, bytes]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def power_cycle(self) -> None:
        for probe in self.probes:
            if probe.pending_address is not None:
                probe.address, probe.pending_address = probe.pending_address, None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        os.close(self.master)
        os.close(self.slave)

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.02)
            if not ready:
                continue
            buffer += os.read(self.master, 256)
            while len(buffer) >= 8:
                frame, buffer = buffer[:8], buffer[8:]
                self.requests.append((time.monotonic(), frame))
                answer = self._answer(frame)
                if answer:
                    os.write(self.master, answer)

    def _answer(self, frame: bytes) -> bytes | None:
        if frame[0] in self.noise_addresses:
            return bytes([0x55, 0xAA, 0x13, 0x7F])
        replies = [r for r in (p.reply(frame) for p in self.probes) if r is not None]
        if not replies:
            return None
        if len(replies) == 1:
            return replies[0]
        # Several probes talk at once: the frame arrives with garbled data.
        first, second = replies[0], replies[1]
        garbled = bytes(a ^ b for a, b in zip(first[3:-2], second[3:-2], strict=True))
        return first[:3] + garbled + first[-2:]


class RecordingRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, int | None]] = []

    async def publish_bus_status(self, state: str, address: int | None) -> bool:
        self.published.append((state, address))
        return True


class FakeBusTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fake = FakeBus()
        self.bus = SoilBus(self.fake.port, timeout=0.2, scan_timeout=0.05, inter_frame_delay=0.002)
        self.database = AsyncMock()
        self.database.load_registry_sensors.return_value = []
        self.database.reserved_addresses.return_value = set()
        self.database.close_absent_placements.return_value = []
        self.database.adopt_probe.side_effect = lambda address: {
            "registry_id": 100 + address,
            "hardware_address": address,
            "device_id": 200 + address,
            "bed_name": None,
            "room_name": None,
        }
        self.redis = RecordingRedis()
        self.wired: list[int] = []
        self.discovery = ProbeDiscovery(
            self.bus,
            self.database,
            self.redis,
            self._wire,
            verify_timeout=0.6,
            verify_interval=0.1,
        )
        self.discovery.startup_scan_done = True

    async def asyncTearDown(self) -> None:
        await self.bus.close()
        self.fake.close()

    async def _wire(self, entry: dict[str, object]) -> None:
        self.wired.append(int(entry["hardware_address"]))  # type: ignore[arg-type]

    def _frames(self, function: int) -> list[bytes]:
        return [frame for _, frame in self.fake.requests if frame[1] == function]

    async def test_factory_probe_is_commissioned_to_lowest_free_address(self) -> None:
        self.fake.probes.append(FakeProbe(address=1))

        await self.discovery.run_cycle()

        self.database.adopt_probe.assert_awaited_once_with(2)
        self.assertEqual(self.wired, [2])
        self.assertEqual(self.redis.published, [("ok", None)])
        self.assertEqual((await self.bus.read_probe(1, scan=True)).status, ModbusStatus.NO_RESPONSE)
        self.assertEqual((await self.bus.read_probe(2)).status, ModbusStatus.OK)

    async def test_reserved_address_is_skipped_and_write_frame_matches_vector(self) -> None:
        self.database.reserved_addresses.return_value = {2}
        self.fake.probes.append(FakeProbe(address=1))

        await self.discovery.run_cycle()

        self.database.adopt_probe.assert_awaited_once_with(3)
        self.assertEqual(self._frames(0x06), [ADDRESS_WRITE_TO_3])

    async def test_startup_scan_adopts_pre_addressed_probe_unchanged(self) -> None:
        self.discovery.startup_scan_done = False
        self.fake.probes.append(FakeProbe(address=7))

        with patch("app.discovery.ASSIGNABLE_ADDRESSES", range(2, 12)):
            await self.discovery.startup_scan()

        self.assertTrue(self.discovery.startup_scan_done)
        self.database.adopt_probe.assert_awaited_once_with(7)
        self.assertEqual(self._frames(0x06), [])
        self.assertEqual(self.fake.probes[0].address, 7)

    async def test_two_factory_probes_become_a_collision_after_three_corrupt_cycles(self) -> None:
        self.fake.probes.append(FakeProbe(address=1))
        self.fake.probes.append(FakeProbe(address=1, registers=(0x0100, 0x00C8, 0x0400, 0x0040)))

        self.assertEqual((await self.bus.read_probe(1, scan=True)).status, ModbusStatus.CORRUPT)
        for _ in range(3):
            await self.discovery.run_cycle()

        self.assertEqual(
            self.redis.published, [("ok", None), ("ok", None), ("collision", None)]
        )
        self.assertEqual(self._frames(0x06), [])
        self.database.adopt_probe.assert_not_awaited()

    async def test_probe_readdressed_only_after_power_cycle(self) -> None:
        self.fake.probes.append(FakeProbe(address=1, readdress_after_power_cycle=True))

        await self.discovery.run_cycle()

        self.assertEqual(self.redis.published[-1], ("power_cycle_required", 2))
        self.database.adopt_probe.assert_not_awaited()

        self.fake.power_cycle()
        await self.discovery.run_cycle()

        self.database.adopt_probe.assert_awaited_once_with(2)
        self.assertEqual(self.redis.published[-1], ("ok", None))
        self.assertIsNone(self.discovery.reserved_address)

    async def test_line_noise_at_factory_address_is_foreign_not_collision(self) -> None:
        self.fake.noise_addresses.add(1)

        self.assertEqual((await self.bus.read_probe(1, scan=True)).status, ModbusStatus.FOREIGN)
        for _ in range(3):
            await self.discovery.run_cycle()

        self.assertEqual(self.redis.published, [("ok", None)] * 3)
        self.assertEqual(self._frames(0x06), [])

    async def test_wiki_vectors_decode_and_frame(self) -> None:
        self.fake.probes.append(FakeProbe(address=1))

        reply = await self.bus.read_probe(1)

        self.assertEqual(self.fake.probes[0].reply(WIKI_READ_REQUEST), WIKI_RESPONSE)
        self.assertEqual(self._frames(0x03), [WIKI_READ_REQUEST])
        self.assertEqual(reply.status, ModbusStatus.OK)
        self.assertEqual(
            decode_sen0604(reply.registers),
            {"humidity": 65.8, "temperature": -10.1, "ec": 1000.0, "ph": 5.6},
        )

    async def test_consecutive_requests_respect_inter_frame_delay(self) -> None:
        bus = SoilBus(self.fake.port, timeout=0.2, scan_timeout=0.05, inter_frame_delay=0.05)
        self.fake.probes.append(FakeProbe(address=2))
        try:
            for _ in range(4):
                self.assertEqual((await bus.read_probe(2)).status, ModbusStatus.OK)
        finally:
            await bus.close()

        times = [at for at, _ in self.fake.requests]
        gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
        self.assertEqual(len(gaps), 3)
        self.assertTrue(all(gap >= 0.045 for gap in gaps), gaps)
