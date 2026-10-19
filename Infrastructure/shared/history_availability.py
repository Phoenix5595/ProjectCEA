"""Reject ordinary history reads after verified source removal.

Closure is not removal. The latest operational hot window remains readable.
Native monitoring scopes each DB statement so its guard and rows share the
same repeatable-read snapshot; concurrent movement cannot return partial data.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from fastapi import HTTPException


class HistoryConnection(Protocol):
    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class LocalHistoryScope:
    room: str | None
    start: datetime
    end: datetime
    source_tables: tuple[str, ...]


_scope: ContextVar[LocalHistoryScope | None] = ContextVar("local_history_scope", default=None)


def current_history_scope() -> LocalHistoryScope | None:
    return _scope.get()


@asynccontextmanager
async def local_history_scope(
    room: str | None, start: datetime, end: datetime, source_tables: Sequence[str]
) -> AsyncIterator[None]:
    """Scope native chart statements; reset across failures and nested calls."""
    token = _scope.set(LocalHistoryScope(room, start, end, tuple(source_tables)))
    try:
        yield
    finally:
        _scope.reset(token)


async def require_local_history(
    conn: HistoryConnection, *, room: str | None, start: datetime, end: datetime,
    source_tables: Sequence[str],
) -> None:
    """Require an intact original-source range, never load a cold/ZIP archive.

    Call within the SAME repeatable-read transaction as the range query.
    Missing migration means no archive removals have yet been activated; only
    that explicit absence permits the legacy query. Other DB failures propagate.
    """
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
        raise ValueError("history availability bounds must be timezone aware")
    if start >= end:
        raise ValueError("history availability bounds must be half-open and nonempty")
    if not source_tables:
        return
    catalog = await conn.fetch("SELECT to_regclass('public.archive_availability') AS relation")
    if not catalog:
        raise RuntimeError("history availability catalog probe returned no row")
    if catalog[0]["relation"] is None:
        return
    rows = await conn.fetch(
        "SELECT state,dataset_id FROM public.archive_availability "
        "WHERE source_rows_removed AND source_table=ANY($1::text[]) "
        "AND ($2::text IS NULL OR room IS NULL OR room=$2) "
        "AND start_at<$4 AND end_at>$3",
        list(source_tables), room, start, end,
    )
    if any(row["state"] == "expired" for row in rows):
        raise HTTPException(status_code=410, detail={"code": "history_expired"})
    dataset_ids = sorted({str(row["dataset_id"]) for row in rows if row["dataset_id"] is not None})
    if dataset_ids:
        raise HTTPException(status_code=409, detail={
            "code": "archive_selection_required", "dataset_ids": dataset_ids,
        })
