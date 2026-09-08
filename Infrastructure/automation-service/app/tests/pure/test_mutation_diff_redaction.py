from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
    use_mutation_context,
)
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import (
    EntityContext,
    OperationalEvent,
    serialize_operational_event,
)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[OperationalEvent] = []

    def emit_nowait(self, event: OperationalEvent) -> None:
        self.events.append(event)


def test_emits_only_allowlisted_changes_and_never_serializes_sensitive_or_note_content() -> None:
    # Given: a committed change with public, secret, and note values.
    secret = "never-serialize-this-secret"
    note = "never-serialize-this-note"
    changes = safe_allowlisted_diff(
        before={"enabled": False, "app_password": secret, "notes": note},
        after={"enabled": True, "app_password": f"{secret}-updated", "notes": "updated note"},
        allowed_fields=frozenset({"enabled", "app_password", "notes"}),
    )
    sink = _RecordingSink()
    context = MutationRequestContext(
        correlation_id=UUID("7552d5f1-0a9a-43e8-a63b-26a60d126c2e"),
        actor_id="api_client",
    )

    # When: the persistence marker emits its one operational mutation event.
    with use_mutation_context(context):
        emitted = emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="calendar_connection", entity_id="primary"),
                changes=changes,
                occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            ),
        )

    # Then: only safe values and note metadata reach the serialized event.
    serialized = serialize_operational_event(sink.events[0])
    assert emitted is True
    assert len(sink.events) == 1
    assert sink.events[0].actor.actor_id == "api_client"
    assert sink.events[0].correlation_id == context.correlation_id
    assert secret not in serialized.decode()
    assert note not in serialized.decode()
    assert b'"key":"enabled"' in serialized
    assert b'"key":"notes_changed"' in serialized
    assert b'"key":"notes_length"' in serialized


def test_returns_without_event_for_an_unchanged_persisted_value() -> None:
    # Given: a successful persistence call whose allowlisted value is unchanged.
    changes = safe_allowlisted_diff(
        before={"enabled": True},
        after={"enabled": True},
        allowed_fields=frozenset({"enabled"}),
    )
    sink = _RecordingSink()

    # When: the marked completion is recorded.
    with use_mutation_context(MutationRequestContext.create()):
        emitted = emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(entity_type="flag", entity_id="example"),
                changes=changes,
                occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            ),
        )

    # Then: a no-op never creates a visible mutation event.
    assert emitted is False
    assert sink.events == []


@pytest.mark.parametrize(
    "field",
    ["password", "token", "api-key", "secret", "credential", "authorization", "app_password"],
)
def test_redacts_every_required_sensitive_field_name(field: str) -> None:
    # Given: an allowlisted changed field whose name is sensitive.
    secret = "never-serialize-this-secret"

    # When: the diff is built for a successful persistence boundary.
    changes = safe_allowlisted_diff(
        before={field: secret},
        after={field: f"{secret}-updated"},
        allowed_fields=frozenset({field}),
    )

    # Then: the field's values and its original name cannot reach the event payload.
    assert changes[0].key == "sensitive_values_changed"
    assert secret not in str(changes)
    assert field not in str(changes)
