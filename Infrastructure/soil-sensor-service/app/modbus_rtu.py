"""Modbus RTU framing over a serial port with classified replies.

Every transaction returns a :class:`ModbusReply`. The status separates a
silent bus (``NO_RESPONSE``), line noise from another source (``FOREIGN``:
the first byte is not the addressed slave, as on a floating RX line), a
frame from the addressed slave that fails validation (``CORRUPT``, e.g. two
probes answering at once) and a valid Modbus exception (``EXCEPTION``).
``serial.SerialException`` propagates to the caller (``SoilBus``).
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import serial

READ_HOLDING_REGISTERS = 0x03
WRITE_SINGLE_REGISTER = 0x06
EXCEPTION_FLAG = 0x80


class ModbusStatus(StrEnum):
    OK = "ok"
    NO_RESPONSE = "no_response"
    CORRUPT = "corrupt"
    FOREIGN = "foreign"
    EXCEPTION = "exception"


@dataclass(frozen=True, slots=True)
class ModbusReply:
    status: ModbusStatus
    registers: tuple[int, ...] = ()


def crc16(data: bytes) -> int:
    """Return the Modbus RTU CRC16 of ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def add_crc(data: bytes) -> bytes:
    """Append the little-endian CRC16 to ``data``."""
    return data + struct.pack("<H", crc16(data))


def crc_valid(frame: bytes) -> bool:
    """Return whether the trailing CRC16 of ``frame`` matches its body."""
    if len(frame) < 3:
        return False
    return struct.unpack("<H", frame[-2:])[0] == crc16(frame[:-2])


class ModbusRTU:
    """Modbus RTU master on one serial port (8N1).

    Example::

        bus = ModbusRTU("/dev/ttyAMA0")
        bus.connect()
        reply = bus.read_holding_registers(2, 0x0000, 4)
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 0.5,
        inter_frame_delay: float = 0.005,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.inter_frame_delay = inter_frame_delay
        self.ser: serial.Serial | None = None
        self._last_io = 0.0

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> None:
        """Open the serial port; raises ``serial.SerialException`` on failure."""
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )

    def disconnect(self) -> None:
        """Close the serial port if open."""
        ser, self.ser = self.ser, None
        if ser is not None and ser.is_open:
            ser.close()

    def read_holding_registers(
        self,
        slave_id: int,
        start_address: int,
        quantity: int,
        timeout: float | None = None,
    ) -> ModbusReply:
        """Read ``quantity`` holding registers (function 0x03)."""
        request = add_crc(struct.pack(">BBHH", slave_id, READ_HOLDING_REGISTERS, start_address, quantity))
        return self._transact(request, timeout, lambda ser: self._read_registers_reply(ser, slave_id, quantity))

    def write_single_register(
        self,
        slave_id: int,
        register: int,
        value: int,
        timeout: float | None = None,
    ) -> ModbusReply:
        """Write one holding register (function 0x06); OK only on an exact echo."""
        request = add_crc(struct.pack(">BBHH", slave_id, WRITE_SINGLE_REGISTER, register, value))
        return self._transact(request, timeout, lambda ser: self._write_reply(ser, slave_id, request))

    def _transact(
        self,
        request: bytes,
        timeout: float | None,
        read_reply: Callable[[serial.Serial], ModbusReply],
    ) -> ModbusReply:
        ser = self.ser
        if ser is None or not ser.is_open:
            raise serial.SerialException(f"{self.port} is not open")
        self._wait_inter_frame_gap()
        previous_timeout = ser.timeout
        if timeout is not None:
            ser.timeout = timeout
        try:
            ser.reset_input_buffer()
            ser.write(request)
            ser.flush()
            self._last_io = time.monotonic()
            return read_reply(ser)
        finally:
            self._last_io = time.monotonic()
            if timeout is not None:
                ser.timeout = previous_timeout

    def _wait_inter_frame_gap(self) -> None:
        remaining = self.inter_frame_delay - (time.monotonic() - self._last_io)
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _read_head(ser: serial.Serial, slave_id: int) -> tuple[ModbusReply | None, bytes]:
        """Read address + function; return a final reply or the 2-byte head."""
        first = ser.read(1)
        if not first:
            return ModbusReply(ModbusStatus.NO_RESPONSE), b""
        if first[0] != slave_id:
            return ModbusReply(ModbusStatus.FOREIGN), b""
        function = ser.read(1)
        if not function:
            return ModbusReply(ModbusStatus.CORRUPT), b""
        return None, first + function

    @staticmethod
    def _exception_reply(ser: serial.Serial, head: bytes) -> ModbusReply:
        frame = head + ser.read(3)
        if len(frame) == 5 and crc_valid(frame):
            return ModbusReply(ModbusStatus.EXCEPTION)
        return ModbusReply(ModbusStatus.CORRUPT)

    def _read_registers_reply(self, ser: serial.Serial, slave_id: int, quantity: int) -> ModbusReply:
        final, head = self._read_head(ser, slave_id)
        if final is not None:
            return final
        function = head[1]
        if function == READ_HOLDING_REGISTERS | EXCEPTION_FLAG:
            return self._exception_reply(ser, head)
        if function != READ_HOLDING_REGISTERS:
            return ModbusReply(ModbusStatus.CORRUPT)
        count = ser.read(1)
        if not count or count[0] != quantity * 2:
            return ModbusReply(ModbusStatus.CORRUPT)
        body = ser.read(count[0] + 2)
        frame = head + count + body
        if len(body) != count[0] + 2 or not crc_valid(frame):
            return ModbusReply(ModbusStatus.CORRUPT)
        data = body[:-2]
        registers = struct.unpack(f">{quantity}H", data)
        return ModbusReply(ModbusStatus.OK, tuple(registers))

    def _write_reply(self, ser: serial.Serial, slave_id: int, request: bytes) -> ModbusReply:
        final, head = self._read_head(ser, slave_id)
        if final is not None:
            return final
        if head[1] == WRITE_SINGLE_REGISTER | EXCEPTION_FLAG:
            return self._exception_reply(ser, head)
        frame = head + ser.read(len(request) - 2)
        if frame == request:
            return ModbusReply(ModbusStatus.OK)
        return ModbusReply(ModbusStatus.CORRUPT)
