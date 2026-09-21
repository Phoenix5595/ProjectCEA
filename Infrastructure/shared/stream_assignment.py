"""CAN stream assignment-snapshot qualification helpers.

Shared between the backend's recent-history stream path and ingestion
tests: every CAN stream entry written after commissioning ships an
assignment snapshot; legacy entries carry none and must never be allowed
to mix locations in a located history.
"""

from __future__ import annotations

from typing import Any


def entry_qualified(entry: dict[str, Any]) -> bool:
    """True when a CAN entry carries qualified assignment metadata.

    Assigned entries must expose a numeric registry id plus location and
    cluster. Explicitly-unassigned entries (``assigned=False``) are
    qualified too — they contribute nothing to a located history but prove
    the interval was not sampled under a stale location. Legacy entries
    (``assigned=None``, written before assignment snapshots existed) are
    unqualified and force the database path.
    """
    assigned = entry.get("assigned")
    if assigned is False:
        return True
    if assigned is not True:
        return False
    if entry.get("registry_id") is None:
        return False
    return entry.get("location") is not None and entry.get("cluster") is not None


def all_entries_qualified(stream_entries: list[dict[str, Any]]) -> bool:
    """True when every CAN entry covering an interval has qualified metadata."""
    return all(entry_qualified(entry) for entry in stream_entries if entry.get("type") == "can")
