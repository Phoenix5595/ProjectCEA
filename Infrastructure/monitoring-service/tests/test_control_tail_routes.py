from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import httpx
import pytest
from fastapi.routing import APIRoute

from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
)
from monitoring_service.control_repository import ControlHistoryDatabase, ControlHistoryRepository
from monitoring_service.main import create_app
from shared.monitoring_contracts import Quality


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@final
class FakeDatabase:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        del arguments
        self.queries.append(query)
        return []


@final
class DenseTailDatabase:
    async def fetch(
        self, query: str, *_: str | int | float | datetime
    ) -> list[dict[str, str | float | int | datetime | None]]:
        if "effective_setpoints" not in query:
            return []
        start = datetime(2026, 8, 20, 11, tzinfo=UTC)
        return [
            {
                "timestamp": start + timedelta(seconds=index),
                "mode": "day",
                "effective_heating_setpoint": float(index),
                "nominal_heating_setpoint": float(index),
                "ramp_progress_heating": None,
                "effective_cooling_setpoint": None,
                "nominal_cooling_setpoint": None,
                "ramp_progress_cooling": None,
                "effective_humidity_setpoint": None,
                "nominal_humidity_setpoint": None,
                "ramp_progress_humidity": None,
                "effective_co2_setpoint": None,
                "nominal_co2_setpoint": None,
                "ramp_progress_co2": None,
                "effective_vpd_setpoint": None,
                "nominal_vpd_setpoint": None,
                "ramp_progress_vpd": None,
                "device_name": None,
                "effective_light_intensity": None,
                "nominal_light_intensity": None,
                "ramp_progress_light": None,
            }
            for index in range(1_001)
        ]


@final
class FakeControlReads:
    def __init__(self, database: ControlHistoryDatabase) -> None:
        self._repository = ControlHistoryRepository(database)

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        return await self._repository.read(location, history_range, max_points)

    async def publications(self, location: str) -> ControlPublicationResponse:
        del location
        return ControlPublicationResponse(
            current=CurrentPublicationResponse(quality=Quality.UNAVAILABLE, value=None),
            projection=ProjectionPublicationResponse(quality=Quality.UNAVAILABLE),
        )


@pytest.mark.anyio
async def test_control_tail_is_registered_and_reads_a_bounded_history_envelope() -> None:
    # Given: canonical control reads backed by a fake read-only database.
    database = FakeDatabase()
    app = create_app(control_reads=FakeControlReads(database))

    # When: the live poller requests a bounded Veg Room tail window.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Veg%20Room/tail",
            params={"start": "2026-08-20T11:00:00Z", "end": "2026-08-20T12:00:00Z"},
        )

    # Then: OpenAPI exposes a distinct tail path and the shared repository returns its envelope.
    assert response.status_code == 200
    assert "/api/monitoring/control/{location}/tail" in app.openapi()["paths"]
    assert response.json()["range"] == {
        "start": "2026-08-20T11:00:00Z",
        "end": "2026-08-20T12:00:00Z",
    }
    assert response.json()["requested_max_points"] is None
    assert response.json()["interval_seconds"] is None
    assert len(database.queries) == 3
    assert any(
        route.path == "/api/monitoring/control/{location}/tail"
        for route in app.routes
        if isinstance(route, APIRoute)
    )


@pytest.mark.anyio
async def test_control_tail_thins_series_when_max_points_is_supplied() -> None:
    # Given: a tail source with more raw setpoint observations than the requested budget.
    app = create_app(control_reads=FakeControlReads(DenseTailDatabase()))

    # When: the live poller requests a budgeted one-hour tail window.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Veg%20Room/tail",
            params={
                "start": "2026-08-20T11:00:00Z",
                "end": "2026-08-20T12:00:00Z",
                "max_points": 1000,
            },
        )

    # Then: the shared history budget retains no more than its requested semantic points.
    assert response.status_code == 200
    payload = response.json()
    series = [*payload["climate"], *payload["lights"], *payload["devices"], *payload["pid"]]
    assert (
        max(len(item["points"]) + len(item["steps"]) + (2 * len(item["linear"])) for item in series)
        <= 1000
    )


@pytest.mark.anyio
async def test_control_tail_rejects_an_unknown_room() -> None:
    # Given: a control application whose reads never see invalid room names.
    app = create_app(control_reads=FakeControlReads(FakeDatabase()))

    # When: the poller asks for a room outside the canonical topology.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Unknown/tail",
            params={"start": "2026-08-20T11:00:00Z", "end": "2026-08-20T12:00:00Z"},
        )

    # Then: callers receive the canonical not-found contract.
    assert response.status_code == 404


@pytest.mark.anyio
async def test_control_tail_rejects_partial_windows() -> None:
    # Given: a control application with canonical read dependencies.
    app = create_app(control_reads=FakeControlReads(FakeDatabase()))

    # When: the poller supplies only one half of its range.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Veg%20Room/tail",
            params={"start": "2026-08-20T11:00:00Z"},
        )

    # Then: it receives the same bad-window response as historical reads.
    assert response.status_code == 400


@pytest.mark.anyio
async def test_control_tail_rejects_inverted_windows_as_client_error() -> None:
    # Given: a control application with canonical read dependencies.
    app = create_app(control_reads=FakeControlReads(FakeDatabase()))

    # When: the caller's window has its start after its end (clock skew).
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/monitoring/control/Veg%20Room/tail",
            params={"start": "2026-08-20T12:00:00Z", "end": "2026-08-20T11:00:00Z"},
        )

    # Then: the request is a client error, not an unhandled server failure.
    assert response.status_code == 400
