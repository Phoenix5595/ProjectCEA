"""Allowlisted before/after mutation diff construction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.events.operational_models import SENSITIVE_KEY_PARTS, FieldChange, JsonValue

_NOTE_FIELDS: Final[frozenset[str]] = frozenset({"note", "notes"})
_SENSITIVE_CHANGE_KEY: Final = "sensitive_values_changed"


def safe_allowlisted_diff(
    before: Mapping[str, JsonValue],
    after: Mapping[str, JsonValue],
    allowed_fields: frozenset[str],
) -> tuple[FieldChange, ...]:
    """Return deterministic safe changes without accepting arbitrary request bodies."""
    changes: list[FieldChange] = []
    sensitive_value_changed = False
    for field in sorted(allowed_fields):
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value == after_value:
            continue
        normalized_field = _normalize_field(field)
        if _is_sensitive_field(normalized_field):
            sensitive_value_changed = True
            continue
        if normalized_field in _NOTE_FIELDS:
            changes.extend(_note_metadata_changes(before_value, after_value))
            continue
        changes.append(FieldChange(key=field, before=before_value, after=after_value))
    if sensitive_value_changed:
        changes.append(FieldChange(key=_SENSITIVE_CHANGE_KEY, before=False, after=True))
    return tuple(changes)


def _note_metadata_changes(before: JsonValue, after: JsonValue) -> tuple[FieldChange, FieldChange]:
    return (
        FieldChange(key="notes_changed", before=False, after=True),
        FieldChange(
            key="notes_length",
            before=_safe_text_length(before),
            after=_safe_text_length(after),
        ),
    )


def _safe_text_length(value: JsonValue) -> int:
    return len(value) if isinstance(value, str) else 0


def _is_sensitive_field(normalized_field: str) -> bool:
    return any(part in normalized_field for part in SENSITIVE_KEY_PARTS)


def _normalize_field(field: str) -> str:
    return field.lower().replace("-", "_")


__all__ = ["safe_allowlisted_diff"]
