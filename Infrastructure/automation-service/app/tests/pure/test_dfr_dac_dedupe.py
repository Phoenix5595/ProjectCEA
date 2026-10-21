from __future__ import annotations

from threading import Event, Thread

import pytest

import app.hardware.dfr0971 as dfr_module
from app.hardware.dfr0971 import DFR0971Driver, DFR0971Manager
from app.hardware.i2c_lock import get_i2c_bus_1_thread_lock


class _FakeBus:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.block = block
        self.entered = Event()
        self.release = Event()
        self.writes: list[tuple[int, int, int]] = []

    def write_word_data(self, address: int, register: int, value: int) -> None:
        self.entered.set()
        if self.block and not self.release.wait(timeout=1):
            raise TimeoutError("fake bus write was not released")
        if self.fail:
            raise OSError("fake I2C write failed")
        self.writes.append((address, register, value))


def _driver(bus: _FakeBus) -> DFR0971Driver:
    driver = DFR0971Driver.__new__(DFR0971Driver)
    driver.i2c_bus = 1
    driver.i2c_address = 0x58
    driver.bus = bus
    driver._i2c_lock = get_i2c_bus_1_thread_lock()
    driver._channel_states = [0.0, 0.0]
    driver._last_dac_code = [None, None]
    driver._last_write_monotonic = [None, None]
    driver._range_set = True
    driver._voltage_range = 10000
    return driver


def test_manager_deduplicates_same_dac_code_and_resyncs_after_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fake DAC bus and monotonic clock starting at zero.
    now = [0.0]
    monkeypatch.setattr(dfr_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(dfr_module.time, "sleep", lambda _seconds: None)
    driver = _driver(_FakeBus())
    manager = DFR0971Manager()
    manager._boards[0] = driver

    # When: the initial code is written, then queried at one second.
    assert manager.needs_write(0, 0, 50.0) is True
    assert manager.set_intensity(0, 0, 50.0) is True
    now[0] = 1.0
    # Under the specified truncation, 50.02% maps to code 2048 rather than 50.0%'s 2047.
    assert manager.needs_write(0, 0, 50.02) is True

    # Then: unchanged and same-code intensities skip; a new code and 60s resync write.
    assert manager.needs_write(0, 0, 50.0) is False
    assert manager.needs_write(0, 0, 50.01) is False
    assert manager.needs_write(0, 0, 50.10) is True
    now[0] = 60.0
    assert manager.needs_write(0, 0, 50.0) is True


def test_failed_dac_write_keeps_manager_write_state_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fake DAC bus that raises during a write.
    monkeypatch.setattr(dfr_module.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(dfr_module.time, "sleep", lambda _seconds: None)
    driver = _driver(_FakeBus(fail=True))
    manager = DFR0971Manager()
    manager._boards[0] = driver

    # When: the manager attempts the initial intensity.
    assert manager.set_intensity(0, 0, 50.0) is False

    # Then: no successful code or timestamp is recorded, so the next tick retries.
    assert driver._last_dac_code == [None, None]
    assert driver._last_write_monotonic == [None, None]
    assert manager.needs_write(0, 0, 50.0) is True


def test_bus_one_thread_lock_serializes_drivers_sharing_the_global_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: two drivers whose fake buses share the process-wide bus-1 lock.
    monkeypatch.setattr(dfr_module.time, "sleep", lambda _seconds: None)
    first_bus = _FakeBus(block=True)
    second_bus = _FakeBus()
    first = _driver(first_bus)
    second = _driver(second_bus)
    results: list[bool] = []
    first_thread = Thread(target=lambda: results.append(first.set_voltage(5.0)))
    second_thread = Thread(target=lambda: results.append(second.set_voltage(6.0)))

    # When: the first I2C call blocks and the second driver starts writing.
    first_thread.start()
    assert first_bus.entered.wait(timeout=1)
    second_thread.start()
    second_entered_while_blocked = second_bus.entered.wait(timeout=0.05)
    first_bus.release.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    # Then: the second bus cannot enter until the first write releases the lock.
    assert second_entered_while_blocked is False
    assert results == [True, True]
    assert len(first_bus.writes) == 1
    assert len(second_bus.writes) == 1
