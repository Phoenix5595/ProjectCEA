from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from monitoring_service.main import create_app
from monitoring_service.readiness import DependencyCheck, StaticReadinessProbe


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_is_live_when_dependencies_are_unavailable() -> None:
    # Given: a service whose read dependencies are unavailable
    app = create_app(
        StaticReadinessProbe(
            database=DependencyCheck(ok=False, detail="pool not initialized"),
            redis=DependencyCheck(ok=False, detail="client not initialized"),
        )
    )

    # When: the liveness endpoint is requested
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    # Then: liveness remains independent of read dependency availability
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "monitoring-service"}


@pytest.mark.anyio
async def test_ready_reports_database_and_redis_independently() -> None:
    # Given: database availability and Redis unavailability
    app = create_app(
        StaticReadinessProbe(
            database=DependencyCheck(ok=True, latency_ms=1.2),
            redis=DependencyCheck(ok=False, detail="client not initialized"),
        )
    )

    # When: the readiness endpoint is requested
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")

    # Then: the response preserves each dependency's state and rejects traffic
    assert response.status_code == 503
    assert response.json() == {
        "service": "monitoring-service",
        "status": "not_ready",
        "checks": {
            "database": {"ok": True, "latency_ms": 1.2, "detail": None},
            "redis": {"ok": False, "latency_ms": None, "detail": "client not initialized"},
        },
    }


def test_service_imports_no_automation_or_hardware_modules() -> None:
    # Given: the monitoring-service package source tree
    package_root = Path(__file__).parents[1] / "monitoring_service"
    sensor_modules = ("sensor_models.py", "sensor_repository.py", "sensor_routes.py")
    prohibited_fragments = ("automation", "hardware", "mcp23017", "dfr0971")

    # When: direct imports are extracted from every module
    imported_modules = {
        alias.name
        for source_file in (package_root / name for name in sensor_modules)
        for node in ast.walk(ast.parse(source_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    imported_modules.update(
        node.module
        for source_file in (package_root / name for name in sensor_modules)
        for node in ast.walk(ast.parse(source_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    # Then: the read service has no control or hardware mutation dependency
    assert not {
        module
        for module in imported_modules
        if any(fragment in module.lower() for fragment in prohibited_fragments)
    }
