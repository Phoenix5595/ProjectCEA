"""Current-run absence, ambiguity, failure and Toronto-day regressions."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.repositories import calendar as calendar_repository
from app.repositories.calendar import CalendarRepository
from app.routes.calendar import get_database, router


class CalendarReadPool:
    def __init__(self, rows=None, error=None):
        self.connection = SimpleNamespace(
            fetch=AsyncMock(return_value=rows or [], side_effect=error)
        )

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["empty", "ambiguous", "unavailable"])
async def test_current_run_distinguishes_absence_ambiguity_and_failure(state):
    rows = (
        [
            {
                "grow_plan_id": UUID("00000000-0000-4000-8000-000000000001"),
                "run_start": date(2026, 10, 3),
                "run_end": date(2026, 10, 3),
            },
            {
                "grow_plan_id": UUID("00000000-0000-4000-8000-000000000002"),
                "run_start": date(2026, 10, 3),
                "run_end": date(2026, 10, 3),
            },
        ]
        if state == "ambiguous"
        else []
    )
    pool = CalendarReadPool(
        rows, RuntimeError("calendar read unavailable") if state == "unavailable" else None
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database] = lambda: SimpleNamespace(
        calendar_repo=CalendarRepository(pool)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/api/calendar/current-run", params={"location": "Flower Room"})
    if state == "empty":
        assert response.status_code == 200
        assert response.json() is None
    elif state == "ambiguous":
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "current_run_ambiguous"
    else:
        assert response.status_code == 500


@pytest.mark.asyncio
async def test_current_run_uses_toronto_day_when_utc_is_already_tomorrow(monkeypatch):
    fixed = datetime(2026, 10, 4, 2, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz)

    monkeypatch.setattr(calendar_repository, "datetime", FrozenDateTime)
    plan = UUID("00000000-0000-4000-8000-000000000001")
    row = {"grow_plan_id": plan, "run_start": date(2026, 10, 3), "run_end": date(2026, 10, 3)}
    pool = CalendarReadPool()
    pool.connection.fetch.side_effect = (
        lambda query, *args: [row] if args[-1] == date(2026, 10, 3) else []
    )
    result = await CalendarRepository(pool).get_current_run("Flower Room")
    assert result is not None
    assert result.grow_plan_id == plan
