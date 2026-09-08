"""Strict, versioned operational-event boundary models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from json import dumps
from math import isfinite
from typing import Annotated, ClassVar, Final, Literal, Self, TypeAlias, assert_never
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.redis_keys import OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES

JsonValue: TypeAlias = str | int | float | bool | None
RedactableJsonValue: TypeAlias = (
    JsonValue | tuple["RedactableJsonValue", ...] | dict[str, "RedactableJsonValue"]
)

SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "api_key",
    "private_key",
)


class EventSource(StrEnum):
    AUTOMATION = "automation"
    API = "api"
    OPERATOR = "operator"
    SYSTEM = "system"
    TRANSPORT = "transport"


class EventCategory(StrEnum):
    RELAY = "relay"
    MANUAL_OVERRIDE = "manual_override"
    RAMP = "ramp"
    CONTROL = "control"
    MUTATION = "mutation"
    ALARM = "alarm"
    SYSTEM = "system"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ActorType(StrEnum):
    OPERATOR = "operator"
    SERVICE = "service"
    SYSTEM = "system"


class FrozenOperationalModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
    )


class EntityContext(FrozenOperationalModel):
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=128)
    cluster: str | None = Field(default=None, max_length=128)


class ActorContext(FrozenOperationalModel):
    actor_type: ActorType
    actor_id: str | None = Field(default=None, max_length=128)


class EventDetail(FrozenOperationalModel):
    key: str = Field(min_length=1, max_length=128)
    value: JsonValue

    @field_validator("key")
    @classmethod
    def reject_sensitive_key(cls, value: str) -> str:
        if _is_sensitive_key(value):
            raise ValueError("sensitive event detail keys are not allowed")
        return value

    @field_validator("value")
    @classmethod
    def reject_non_finite_numbers(cls, value: JsonValue) -> JsonValue:
        _validate_json_value(value)
        return value


class FieldChange(FrozenOperationalModel):
    key: str = Field(min_length=1, max_length=128)
    before: JsonValue = None
    after: JsonValue = None

    @field_validator("key")
    @classmethod
    def reject_sensitive_key(cls, value: str) -> str:
        if _is_sensitive_key(value):
            raise ValueError("sensitive diff keys are not allowed")
        return value

    @field_validator("before", "after")
    @classmethod
    def reject_non_finite_numbers(cls, value: JsonValue) -> JsonValue:
        _validate_json_value(value)
        return value


class RelayPayload(FrozenOperationalModel):
    family: Literal["relay"] = "relay"
    state: bool | None = None
    observed_state: bool | None = None
    command_mode: str | None = Field(default=None, max_length=64)
    details: tuple[EventDetail, ...] = ()


class ManualOverridePayload(FrozenOperationalModel):
    family: Literal["manual_override"] = "manual_override"
    mode: str = Field(min_length=1, max_length=64)
    expires_at: AwareDatetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0)

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry_to_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class RampPayload(FrozenOperationalModel):
    family: Literal["ramp"] = "ramp"
    ramp_type: str = Field(min_length=1, max_length=64)
    start_value: float | None = None
    target_value: float | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    phase: str | None = Field(default=None, max_length=64)


class ControlPayload(FrozenOperationalModel):
    family: Literal["control"] = "control"
    controller: str = Field(min_length=1, max_length=64)
    sensor_value: float | None = None
    effective_setpoint: float | None = None
    error: float | None = None
    output_percent: float | None = Field(default=None, ge=0, le=100)


class MutationPayload(FrozenOperationalModel):
    family: Literal["mutation"] = "mutation"
    operation: str = Field(min_length=1, max_length=64)
    changes: tuple[FieldChange, ...]


class AlarmPayload(FrozenOperationalModel):
    family: Literal["alarm"] = "alarm"
    alarm_code: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(default=None, max_length=512)


class SystemPayload(FrozenOperationalModel):
    family: Literal["system"] = "system"
    component: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(default=None, max_length=8_192)
    details: tuple[EventDetail, ...] = ()


OperationalEventPayload: TypeAlias = Annotated[
    RelayPayload
    | ManualOverridePayload
    | RampPayload
    | ControlPayload
    | MutationPayload
    | AlarmPayload
    | SystemPayload,
    Field(discriminator="family"),
]


class OperationalEvent(FrozenOperationalModel):
    schema_version: Literal[1] = 1
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: AwareDatetime
    source: EventSource
    category: EventCategory
    severity: EventSeverity
    event_type: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
    )
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    entity: EntityContext | None = None
    actor: ActorContext | None = None
    reason_code: str | None = Field(default=None, max_length=128)
    reason_text: str | None = Field(default=None, max_length=512)
    payload: OperationalEventPayload

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurrence_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_matching_payload_category(self) -> Self:
        if self.category is not _payload_category(self.payload):
            raise ValueError("event category must match its payload family")
        return self


class OperationalEventSerializationError(ValueError):
    def __init__(self, byte_count: int) -> None:
        super().__init__(
            "operational event exceeds the serialized byte limit "
            f"({byte_count} > {OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES})"
        )


def serialize_operational_event(event: OperationalEvent) -> bytes:
    serialized = dumps(
        event.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(serialized) > OPERATIONAL_EVENT_MAX_SERIALIZED_BYTES:
        raise OperationalEventSerializationError(len(serialized))
    return serialized


def redact_sensitive_values(value: RedactableJsonValue) -> RedactableJsonValue:
    match value:
        case dict() as mapping:
            return {
                key: "[REDACTED]" if _is_sensitive_key(key) else redact_sensitive_values(item)
                for key, item in mapping.items()
            }
        case tuple() as values:
            return tuple(redact_sensitive_values(item) for item in values)
        case _:
            return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _validate_json_value(value: JsonValue) -> None:
    match value:
        case float() if not isfinite(value):
            raise ValueError("event payload numbers must be finite")
        case _:
            return


def _payload_category(payload: OperationalEventPayload) -> EventCategory:
    match payload:
        case RelayPayload():
            return EventCategory.RELAY
        case ManualOverridePayload():
            return EventCategory.MANUAL_OVERRIDE
        case RampPayload():
            return EventCategory.RAMP
        case ControlPayload():
            return EventCategory.CONTROL
        case MutationPayload():
            return EventCategory.MUTATION
        case AlarmPayload():
            return EventCategory.ALARM
        case SystemPayload():
            return EventCategory.SYSTEM
        case unreachable:
            assert_never(unreachable)


__all__ = [
    "ActorContext",
    "ActorType",
    "AlarmPayload",
    "ControlPayload",
    "EntityContext",
    "EventCategory",
    "EventDetail",
    "EventSeverity",
    "EventSource",
    "FieldChange",
    "JsonValue",
    "ManualOverridePayload",
    "MutationPayload",
    "OperationalEvent",
    "OperationalEventPayload",
    "OperationalEventSerializationError",
    "RampPayload",
    "RedactableJsonValue",
    "RelayPayload",
    "SystemPayload",
    "redact_sensitive_values",
    "serialize_operational_event",
]
