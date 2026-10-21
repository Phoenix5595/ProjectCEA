from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.background_tasks as background_tasks_module
from app.background_tasks import BackgroundTasks


def _background_tasks() -> BackgroundTasks:
    tasks = BackgroundTasks.__new__(BackgroundTasks)
    tasks._running = True
    tasks._task = SimpleNamespace(done=lambda: False)
    tasks._last_tick_completed_at = None
    tasks.control_engine = SimpleNamespace(config=SimpleNamespace(get_update_interval=lambda: 2))
    tasks.update_interval = 2
    return tasks


def test_control_loop_check_reports_never_ticked() -> None:
    tasks = _background_tasks()

    assert tasks.control_loop_check() == {
        "ok": False,
        "detail": "no completed control tick",
    }


def test_control_loop_check_reports_fresh_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background_tasks_module.time, "monotonic", lambda: 100.0)
    tasks = _background_tasks()
    tasks._last_tick_completed_at = 98.25

    assert tasks.control_loop_check() == {"ok": True, "age_ms": 1750.0}


def test_control_loop_check_reports_stale_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background_tasks_module.time, "monotonic", lambda: 100.0)
    tasks = _background_tasks()
    tasks._last_tick_completed_at = 93.9

    assert tasks.control_loop_check() == {
        "ok": False,
        "detail": "last control tick 6.1s ago (limit 6s)",
    }


def test_control_loop_check_reports_finished_task() -> None:
    tasks = _background_tasks()
    tasks._last_tick_completed_at = 100.0
    tasks._task = SimpleNamespace(done=lambda: True)

    assert tasks.control_loop_check() == {
        "ok": False,
        "detail": "control loop task not running",
    }


def test_control_loop_check_reports_stopped_loop() -> None:
    tasks = _background_tasks()
    tasks._running = False

    assert tasks.control_loop_check() == {
        "ok": False,
        "detail": "control loop task not running",
    }


@pytest.mark.asyncio
async def test_scheduler_retry_loads_and_opens_startup_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = BackgroundTasks.__new__(BackgroundTasks)
    tasks._running = True
    tasks._scheduler_ready = asyncio.Event()
    attempts = 0
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def load_scheduler_data() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        tasks._running = False

    tasks._load_scheduler_data = load_scheduler_data
    monkeypatch.setattr(
        background_tasks_module,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep),
    )

    await tasks._retry_scheduler_load()

    assert attempts == 2
    assert delays == [5, 5]
    assert tasks._scheduler_ready.is_set()
