from __future__ import annotations

from datetime import timedelta

import anyio
import asyncpg
import pytest

from app.monitoring_publication.workers import MonitoringPublicationWorkers


@pytest.mark.asyncio
async def test_database_interface_error_is_retried_and_worker_recovers() -> None:
    attempts = 0
    failed = anyio.Event()
    succeeded = anyio.Event()

    async def current_publish() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed.set()
            raise asyncpg.InterfaceError("x")
        succeeded.set()
        return True

    async def projection_publish() -> bool:
        return True

    workers = MonitoringPublicationWorkers(
        current_publish,
        projection_publish,
        interval=timedelta(milliseconds=5),
    )
    await workers.start()
    try:
        with anyio.fail_after(1):
            await failed.wait()
            await succeeded.wait()

        health = workers.health.current
        assert health.failed_runs == 1
        assert health.last_error == "InterfaceError: x"
        assert health.last_success_at is not None
    finally:
        await workers.stop()


@pytest.mark.asyncio
async def test_publication_database_error_does_not_cancel_host_task() -> None:
    async def current_publish() -> bool:
        raise asyncpg.InterfaceError("x")

    async def projection_publish() -> bool:
        return True

    workers = MonitoringPublicationWorkers(
        current_publish,
        projection_publish,
        interval=timedelta(milliseconds=5),
    )
    host_completed = False

    async def host_task() -> None:
        nonlocal host_completed
        await workers.start()
        try:
            await anyio.sleep(0.025)
            host_completed = True
        finally:
            await workers.stop()

    await host_task()

    assert host_completed
    assert workers.health.current.failed_runs > 0
