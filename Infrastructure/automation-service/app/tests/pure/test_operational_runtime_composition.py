from __future__ import annotations

from fastapi import FastAPI

from app.routes.routes import register_routes


def test_registered_runtime_routes_include_operational_event_routes() -> None:
    # Given: the complete central route registry.
    app = FastAPI()

    # When: all routes are registered before the application starts.
    register_routes(app)

    # Then: the central registry owns the event API lanes.
    assert {"/api/events/history", "/api/events/stream"} <= {route.path for route in app.routes}
