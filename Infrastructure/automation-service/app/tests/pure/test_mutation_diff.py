"""Behaviour of allowlisted mutation diffs for nested and list values."""

from __future__ import annotations

from app.events.mutation_diff import safe_allowlisted_diff


def _triples(changes):
    return [(change.key, change.before, change.after) for change in changes]


def test_nested_mappings_flatten_to_dotted_keys_and_skip_equal_leaves():
    changes = safe_allowlisted_diff(
        before={"limits": {"max": 35, "min": 10, "nested": {"a": 1}}},
        after={"limits": {"max": 30, "min": 10, "nested": {"a": 2, "b": True}}},
        allowed_fields=frozenset({"limits"}),
    )

    assert _triples(changes) == [
        ("limits.max", 35, 30),
        ("limits.nested.a", 1, 2),
        ("limits.nested.b", None, True),
    ]


def test_mapping_replacing_scalar_reports_every_new_leaf():
    changes = safe_allowlisted_diff(
        before={}, after={"limits": {"max": 30}}, allowed_fields=frozenset({"limits"})
    )

    assert _triples(changes) == [("limits.max", None, 30)]


def test_lists_compare_by_equality_and_report_canonical_json():
    unchanged = safe_allowlisted_diff(
        before={"days": [1, 2]}, after={"days": (1, 2)}, allowed_fields=frozenset({"days"})
    )
    changed = safe_allowlisted_diff(
        before={"days": [1, 2]},
        after={"days": [{"b": 1, "a": 2}]},
        allowed_fields=frozenset({"days"}),
    )

    assert _triples(unchanged) == []
    assert _triples(changed) == [("days", "[1,2]", '[{"a":2,"b":1}]')]


def test_sensitive_and_note_rules_apply_to_nested_leaves():
    changes = safe_allowlisted_diff(
        before={"auth": {"api_key": "old", "notes": "ab"}},
        after={"auth": {"api_key": "new", "notes": "abcd"}},
        allowed_fields=frozenset({"auth"}),
    )

    assert _triples(changes) == [
        ("notes_changed", False, True),
        ("notes_length", 2, 4),
        ("sensitive_values_changed", False, True),
    ]
