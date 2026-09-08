from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_dependencies import (
    get_mutation_event_sink,
    get_mutation_request_context,
)
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext, OperationalEvent
from app.events.operational_ports import OperationalEventSink


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


def _mutation_app() -> FastAPI:
    app = FastAPI()

    @app.put("/mutation")
    async def mutation(
        context: Annotated[MutationRequestContext, Depends(get_mutation_request_context)],
        sink: Annotated[OperationalEventSink, Depends(get_mutation_event_sink)],
    ) -> dict[str, str | bool]:
        emitted = emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="flag", entity_id="example"),
                changes=safe_allowlisted_diff(
                    before={"enabled": False},
                    after={"enabled": True},
                    allowed_fields=frozenset({"enabled"}),
                ),
            ),
            context=context,
        )
        return {
            "emitted": emitted,
            "correlation_id": str(context.correlation_id),
            "actor": context.actor.actor_id or "",
        }

    return app


@pytest.mark.asyncio
async def test_default_dependencies_supply_request_context_and_safe_noop_sink() -> None:
    # Given: an isolated route with no Todo 15 runtime wiring.
    app = _mutation_app()
    correlation_id = "7552d5f1-0a9a-43e8-a63b-26a60d126c2e"

    # When: a route performs a successful mutation through its default dependencies.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/mutation", headers={"X-Request-ID": correlation_id})

    # Then: the default sink is safe and the route receives fixed actor/correlation context.
    assert response.status_code == 200
    assert response.json() == {
        "emitted": True,
        "correlation_id": correlation_id,
        "actor": "api_client",
    }


@pytest.mark.asyncio
async def test_dependency_overrides_replace_context_and_sink_without_runtime_wiring() -> None:
    # Given: Todo 15's future dependency override shape with a recording sink.
    app = _mutation_app()
    context = MutationRequestContext(correlation_id=UUID("ea489a38-0599-4ba1-9fb8-ca4a912bb873"))
    sink = _RecordingSink()
    app.dependency_overrides[get_mutation_request_context] = lambda: context
    app.dependency_overrides[get_mutation_event_sink] = lambda: sink

    # When: the same route completes a persisted mutation.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/mutation")

    # Then: the override controls both emitted event context and the receiving sink.
    assert response.status_code == 200
    assert len(sink.events) == 1
    assert sink.events[0].correlation_id == context.correlation_id
    assert sink.events[0].actor.actor_id == "api_client"
