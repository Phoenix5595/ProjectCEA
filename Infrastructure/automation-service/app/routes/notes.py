"""Notes API: persist zone/mode notes in a directory outside deploy so they survive deploys."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["notes"])

# Directory outside release so notes persist across deploys (e.g. /var/lib/projectcea/notes)
NOTES_DATA_DIR = os.getenv("NOTES_DATA_DIR", "/var/lib/projectcea/notes")


def _sanitize(s: str) -> str:
    """Safe filename segment: alphanumeric and underscore only."""
    return re.sub(r"[^\w\-]", "_", s, flags=re.ASCII) or "default"


def _notes_path(location: str, cluster: str, mode: str) -> Path:
    segs = [_sanitize(location), _sanitize(cluster), _sanitize(mode)]
    return Path(NOTES_DATA_DIR) / f"{segs[0]}_{segs[1]}_{segs[2]}.txt"


class NotesBody(BaseModel):
    content: str


@router.get("/notes/{location}/{cluster}/{mode}")
async def get_notes(location: str, cluster: str, mode: str) -> dict[str, str]:
    """Get notes for a location/cluster/mode. Returns { content: string } (empty if none)."""
    path = _notes_path(location, cluster, mode)
    if not path.exists():
        return {"content": ""}
    try:
        text = path.read_text(encoding="utf-8")
        return {"content": text}
    except OSError as e:
        logger.warning(f"Failed to read notes {path}: {e}")
        return {"content": ""}


@router.put("/notes/{location}/{cluster}/{mode}")
@emits_operational_mutation
async def save_notes(
    location: str,
    cluster: str,
    mode: str,
    body: NotesBody,
    context: Annotated[MutationRequestContext, Depends(get_mutation_request_context)],
    sink: Annotated[OperationalEventSink, Depends(get_mutation_event_sink)],
) -> dict[str, str]:
    """Save notes for a location/cluster/mode. Stored under NOTES_DATA_DIR (persists across deploys)."""
    path = _notes_path(location, cluster, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous_content = path.read_text(encoding="utf-8") if path.exists() else ""
        content = body.content or ""
        path.write_text(content, encoding="utf-8")
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="update",
                entity=EntityContext(
                    entity_type="note",
                    entity_id=f"{location}/{cluster}/{mode}",
                    location=location,
                    cluster=cluster,
                ),
                changes=safe_allowlisted_diff(
                    before={"notes": previous_content},
                    after={"notes": content},
                    allowed_fields=frozenset({"notes"}),
                ),
            ),
            context=context,
        )
        return {"content": content}
    except OSError as e:
        logger.error(f"Failed to write notes {path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save notes") from e
