"""Allowlisted before/after mutation diff construction."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
import json
from typing import Final

from app.events.operational_models import SENSITIVE_KEY_PARTS, FieldChange, JsonValue

_NOTE_FIELDS: Final[frozenset[str]] = frozenset({"note", "notes"})
_SENSITIVE_CHANGE_KEY: Final = "sensitive_values_changed"


def safe_allowlisted_diff(
    before: Mapping[str, object],
    after: Mapping[str, object],
    allowed_fields: frozenset[str],
) -> tuple[FieldChange, ...]:
    """Return deterministic safe changes without accepting arbitrary request bodies.

    Nested mappings are flattened into dotted keys (``safety_limits.max_temperature``);
    lists and tuples are compared by equality and reported as canonical JSON strings.
    """
    changes: list[FieldChange] = []
    sensitive_value_changed = False
    for field in sorted(allowed_fields):
        for key, before_value, after_value in _leaf_changes(
            field, before.get(field), after.get(field)
        ):
            normalized_key = _normalize_field(key)
            if _is_sensitive_field(normalized_key):
                sensitive_value_changed = True
                continue
            if normalized_key.rsplit(".", 1)[-1] in _NOTE_FIELDS:
                changes.extend(_note_metadata_changes(before_value, after_value))
                continue
            changes.append(FieldChange(key=key, before=before_value, after=after_value))
    if sensitive_value_changed:
        changes.append(FieldChange(key=_SENSITIVE_CHANGE_KEY, before=False, after=True))
    return tuple(changes)


def _leaf_changes(
    key: str, before: object, after: object
) -> Iterator[tuple[str, JsonValue, JsonValue]]:
    if isinstance(before, Mapping) or isinstance(after, Mapping):
        before_map = before if isinstance(before, Mapping) else {}
        after_map = after if isinstance(after, Mapping) else {}
        for sub in sorted({str(name) for name in (*before_map, *after_map)}):
            yield from _leaf_changes(f"{key}.{sub}", before_map.get(sub), after_map.get(sub))
        return
    before_leaf, after_leaf = _leaf_value(before), _leaf_value(after)
    if before_leaf == after_leaf:
        return
    yield key, before_leaf, after_leaf


def _leaf_value(value: object) -> JsonValue:
    if isinstance(value, list | tuple):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value  # type: ignore[return-value]


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
