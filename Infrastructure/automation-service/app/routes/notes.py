"""Notes API: persist zone/mode notes in a directory outside deploy so they survive deploys."""

from __future__ import annotations

import os
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.logging import get_logger

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
async def save_notes(location: str, cluster: str, mode: str, body: NotesBody) -> dict[str, str]:
    """Save notes for a location/cluster/mode. Stored under NOTES_DATA_DIR (persists across deploys)."""
    path = _notes_path(location, cluster, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(body.content or "", encoding="utf-8")
        return {"content": body.content or ""}
    except OSError as e:
        logger.error(f"Failed to write notes {path}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save notes") from e
