from __future__ import annotations

from datetime import UTC, datetime
from math import nan
from uuid import UUID

from pydantic import ValidationError
import pytest

from app.events.operational_models import (
    ActorContext,
    ActorType,
    AlarmPayload,
    ControlPayload,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    FieldChange,
    ManualOverridePayload,
    MutationPayload,
    OperationalEvent,
    OperationalEventPayload,
    OperationalEventSerializationError,
    RampPayload,
    RelayPayload,
    SystemPayload,
    redact_sensitive_values,
    serialize_operational_event,
)
from shared.redis_keys import (
    OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES,
    OPERATIONAL_EVENTS_MAXLEN,
    OPERATIONAL_EVENTS_RETENTION_MS,
    OPERATIONAL_EVENTS_STREAM,
    operational_events_stream,
)


def _occurred_at() -> datetime:
    return datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _event(category: EventCategory, payload: OperationalEventPayload) -> OperationalEvent:
    return OperationalEvent(
        event_id=UUID("ea489a38-0599-4ba1-9fb8-ca4a912bb873"),
        occurred_at=_occurred_at(),
        source=EventSource.AUTOMATION,
        category=category,
        severity=EventSeverity.INFO,
        event_type="relay.state_changed",
        correlation_id=UUID("6e7aa751-25cc-4610-854c-4b622a165d3c"),
        entity=EntityContext(entity_type="relay", entity_id="heater-1", location="Veg Room"),
        actor=ActorContext(actor_type=ActorType.SERVICE, actor_id="automation-service"),
        payload=payload,
    )


@pytest.mark.parametrize(
    ("category", "payload"),
    [
        (EventCategory.RELAY, RelayPayload(state=True, observed_state=True)),
        (
            EventCategory.MANUAL_OVERRIDE,
            ManualOverridePayload(mode="TIMED_ON", expires_at=_occurred_at()),
        ),
        (EventCategory.RAMP, RampPayload(ramp_type="light", start_value=0.0, target_value=100.0)),
        (EventCategory.CONTROL, ControlPayload(controller="pid", output_percent=42.0)),
        (
            EventCategory.MUTATION,
            MutationPayload(operation="update", changes=(FieldChange(key="enabled", after=True),)),
        ),
        (EventCategory.ALARM, AlarmPayload(alarm_code="sensor.stale", state="opened")),
        (EventCategory.SYSTEM, SystemPayload(component="redis", state="connected")),
    ],
)
def test_round_trips_every_discriminated_payload_family(
    category: EventCategory, payload: object
) -> None:
    # Given: each supported typed payload family.
    event = _event(category, payload)

    # When: the versioned envelope crosses its JSON boundary.
    serialized = serialize_operational_event(event)
    restored = OperationalEvent.model_validate_json(serialized)

    # Then: the exact typed envelope and variant survive intact.
    assert restored == event


def test_serializes_relay_event_deterministically_with_utc_identity() -> None:
    # Given: one concrete relay transition with a correlation identity.
    event = _event(EventCategory.RELAY, RelayPayload(state=True, observed_state=True))

    # When: it is serialized twice for the Redis stream.
    first = serialize_operational_event(event)
    second = serialize_operational_event(event)

    # Then: byte output is stable and contains the normalized v1 identity.
    assert first == second
    assert b'"schema_version":1' in first
    assert b'"occurred_at":"2026-09-01T12:00:00Z"' in first
    assert b'"correlation_id":"6e7aa751-25cc-4610-854c-4b622a165d3c"' in first


def test_rejects_naive_timestamps_unknown_sources_and_non_finite_numbers() -> None:
    # Given: malformed producer inputs at the event boundary.
    payload = RelayPayload(state=True)

    # When / Then: each invalid trust-boundary value is rejected.
    with pytest.raises(ValidationError):
        OperationalEvent.model_validate(
            {
                **_event(EventCategory.RELAY, payload).model_dump(),
                "occurred_at": datetime(2026, 9, 1, 12, 0),
            }
        )
    with pytest.raises(ValidationError):
        OperationalEvent.model_validate(
            {
                **_event(EventCategory.RELAY, payload).model_dump(),
                "source": "unknown-producer",
            }
        )
    with pytest.raises(ValidationError):
        ControlPayload(controller="pid", output_percent=nan)


def test_rejects_secret_diff_keys_without_leaking_secret_values() -> None:
    # Given: a persisted mutation attempting to include a credential value.
    secret_value = "never-serialize-this-secret"

    # When / Then: the unsafe key fails validation without exposing its value.
    with pytest.raises(ValidationError) as error:
        MutationPayload(
            operation="update",
            changes=(FieldChange(key="app_password", after=secret_value),),
        )
    assert secret_value not in str(error.value)


@pytest.mark.parametrize("key", ["app_password", "api_token", "authorization", "credential"])
def test_redacts_each_secret_key_fixture(key: str) -> None:
    # Given: an untrusted JSON-shaped field map.
    value = {key: "never-serialize-this-secret", "enabled": True}

    # When: it is prepared for safe diagnostic output.
    redacted = redact_sensitive_values(value)

    # Then: secret-bearing values are replaced while safe values remain visible.
    assert redacted == {key: "[REDACTED]", "enabled": True}


def test_rejects_serialized_output_over_the_canonical_size_limit() -> None:
    # Given: an otherwise valid system event exceeding the stream byte contract.
    event = _event(
        EventCategory.SYSTEM,
        SystemPayload(component="redis", state="connected", detail="x" * 4_097),
    )

    # When / Then: serialization rejects it without materializing payload text in the error.
    with pytest.raises(OperationalEventSerializationError) as error:
        serialize_operational_event(event)
    assert "x" * 32 not in str(error.value)


def test_exposes_canonical_operational_stream_bounds() -> None:
    # Given: the shared Redis namespace contract.
    # When: callers resolve the global operational stream.
    stream = operational_events_stream()

    # Then: all consumers receive the one bounded canonical stream.
    assert stream == OPERATIONAL_EVENTS_STREAM == "cea:events:operational"
    assert OPERATIONAL_EVENTS_RETENTION_MS == 86_400_000
    assert OPERATIONAL_EVENTS_MAXLEN == 50_000
    assert OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES == 4_096
