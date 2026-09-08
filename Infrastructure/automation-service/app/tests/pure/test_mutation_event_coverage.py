from __future__ import annotations

from fastapi import FastAPI
import pytest

from app.events.mutation_coverage import assert_mutation_route_coverage
from app.routes.routes import register_routes


def test_application_startup_coverage_gate_accepts_semantic_device_command_delegates() -> None:
    # Given: the production application assembled through its sole route boundary.

    app = FastAPI()
    register_routes(app)

    # When: the startup coverage guard is evaluated against the real registry.
    assert_mutation_route_coverage(app)

    # Then: every mutating route has a marker or a reviewed exclusion.
