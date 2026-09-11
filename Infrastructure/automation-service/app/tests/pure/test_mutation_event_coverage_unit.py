from __future__ import annotations

from inspect import getsource

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
    mutation_context_middleware,
)
from app.events.mutation_coverage import (
    MutationCoverageError,
    assert_mutation_route_coverage,
    emits_operational_mutation,
)
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.mutation_exclusions import MUTATION_ROUTE_EXCLUSIONS, NonPersistentEffect
from app.events.operational_models import EntityContext, OperationalEvent
from app.routes import flags, hardware, timing
from app.routes.lights import light_control


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_marked_success_emits_once_and_errors_emit_nothing() -> None:
    # Given: a small app with one marked committed route, one no-op, and one failed route.
    app = FastAPI()
    sink = _RecordingSink()
    app.middleware("http")(mutation_context_middleware)

    @app.put("/devices/{device_id}", operation_id="update_device")
    @emits_operational_mutation
    async def update_device(device_id: str) -> dict[str, bool]:
        changes = safe_allowlisted_diff(
            before={"enabled": False},
            after={"enabled": True},
            allowed_fields=frozenset({"enabled"}),
        )
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="device", entity_id=device_id),
                changes=changes,
            ),
        )
        return {"updated": True}

    @app.put("/devices/{device_id}/noop", operation_id="noop_device")
    @emits_operational_mutation
    async def noop_device(device_id: str) -> dict[str, str]:
        changes = safe_allowlisted_diff(
            before={"enabled": True},
            after={"enabled": True},
            allowed_fields=frozenset({"enabled"}),
        )
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="device", entity_id=device_id),
                changes=changes,
            ),
        )
        return {"device_id": device_id}

    @app.put("/devices/{device_id}/failure", operation_id="failing_device")
    @emits_operational_mutation
    async def failing_device(device_id: str) -> dict[str, str]:
        del device_id
        raise HTTPException(status_code=500, detail="repository failed")

    # When: each completion outcome is driven through the HTTP middleware surface.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        successful = await client.put(
            "/devices/one", headers={"X-Request-ID": "7552d5f1-0a9a-43e8-a63b-26a60d126c2e"}
        )
        no_op = await client.put("/devices/two/noop")
        failed = await client.put("/devices/three/failure")

    # Then: only the committed, changed operation emits exactly one safe event.
    assert successful.status_code == 200
    assert no_op.status_code == 200
    assert failed.status_code == 500
    assert len(sink.events) == 1
    assert sink.events[0].correlation_id == MutationRequestContext.parse_correlation_id(
        "7552d5f1-0a9a-43e8-a63b-26a60d126c2e"
    )
    assert sink.events[0].actor.actor_id == "api_client"


def test_reports_a_synthetic_unmarked_mutation_by_operation_and_source_file() -> None:
    # Given: an app containing one unmarked mutating operation.
    app = FastAPI()

    @app.post("/unmarked", operation_id="unmarked_operation")
    async def unmarked() -> dict[str, bool]:
        return {"ok": True}

    # When / Then: coverage fails with a reviewable operation and source location.
    with pytest.raises(MutationCoverageError) as error:
        assert_mutation_route_coverage(app)
    assert "unmarked_operation" in str(error.value)
    assert __file__ in str(error.value)


def test_accepts_reviewed_nonpersistent_exclusions_only_for_permitted_operation_kinds() -> None:
    # Given: the reviewed map of diagnostic, cache, timing, and test-command endpoints.
    excluded_paths = {exclusion.path for exclusion in MUTATION_ROUTE_EXCLUSIONS}

    # When: the declared exclusions are inspected as the coverage-checker input.
    accepted = {
        "/api/climate-timeline/{location}/{cluster}/preview",
        "/api/timing/reset",
        "/api/flags/cache/clear",
        "/api/hardware/relays/test",
        "/api/calendar/sync/connections/test",
        "/api/lights/{location}/{cluster}/{device_name}/intensity",
        "/api/lights/{location}/{cluster}/{device_name}/voltage",
    }

    # Then: every exclusion is narrowly justified and no persistence route is exempted.
    assert excluded_paths == accepted
    assert all(exclusion.rationale for exclusion in MUTATION_ROUTE_EXCLUSIONS)


def test_rejects_the_persisted_light_test_route_from_the_exclusion_map() -> None:
    # Given: the light-test route writes authoritative device state during its sweep.
    excluded_paths = {exclusion.path for exclusion in MUTATION_ROUTE_EXCLUSIONS}

    # When: the reviewed non-persistent map is inspected.
    light_test_path = "/api/lights/{device_id}/test"

    # Then: Todo 10 must mark that persisted mutation instead of excluding it.
    assert light_test_path not in excluded_paths


@pytest.mark.parametrize(
    ("path", "handler"),
    [
        ("/api/timing/reset", timing.reset_timing_data),
        ("/api/flags/cache/clear", flags.clear_cache),
        ("/api/hardware/relays/test", hardware.relay_test),
        (
            "/api/lights/{location}/{cluster}/{device_name}/intensity",
            light_control.set_intensity,
        ),
        (
            "/api/lights/{location}/{cluster}/{device_name}/voltage",
            light_control.set_voltage,
        ),
    ],
)
def test_every_exclusion_has_a_nonpersistent_handler_proof(path: str, handler) -> None:
    # Given: each remaining reviewed exemption and its concrete route handler.
    exclusion = next(item for item in MUTATION_ROUTE_EXCLUSIONS if item.path == path)

    # When: the handler's structural persistence seams are inspected.
    source = getsource(handler)

    # Then: the declared local/transient effect has no authoritative write seam.
    assert exclusion.effect in {
        NonPersistentEffect.LOCAL_TIMING_RESET,
        NonPersistentEffect.LOCAL_CACHE_CLEAR,
        NonPersistentEffect.TRANSIENT_HARDWARE_TEST,
        NonPersistentEffect.TRANSIENT_HARDWARE_COMMAND,
        NonPersistentEffect.TRANSIENT_REMOTE_CONNECTION_PROBE,
    }
    assert "device_repo." not in source
    assert "repository." not in source
    assert "database" not in source
    assert "redis" not in source
    assert "emit_persisted_mutation" not in source
    assert ".execute(" not in source
    assert ".commit(" not in source


def test_accepts_an_unmarked_synthetic_route_only_when_its_exact_exclusion_is_reviewed() -> None:
    # Given: a synthetic timing reset matching one narrowly reviewed nonpersistent route.
    app = FastAPI()

    @app.post("/api/timing/reset", operation_id="reset_timing")
    async def reset_timing() -> dict[str, bool]:
        return {"reset": True}

    # When: the mutating OpenAPI operation is checked against the reviewed map.
    assert_mutation_route_coverage(app)

    # Then: no marker is required because the exact route/method is an approved exclusion.
