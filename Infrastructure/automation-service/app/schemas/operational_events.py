"""HTTP response schemas for the operational-event replay API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.events.operational_models import OperationalEvent


class OperationalEventItem(BaseModel):
    """One immutable event paired with its Redis stream cursor."""

    model_config = ConfigDict(frozen=True)

    redis_id: str = Field(pattern=r"^\d+-\d+$")
    event: OperationalEvent


class OperationalEventScan(BaseModel):
    """Bounded scan accounting for filtered event-history pages."""

    model_config = ConfigDict(frozen=True)

    scanned: int = Field(ge=0)
    limit: int = Field(ge=1)


class OperationalEventHistory(BaseModel):
    """A cursor page of operational events and retained-stream bounds."""

    model_config = ConfigDict(frozen=True)

    items: tuple[OperationalEventItem, ...]
    newest_cursor: str | None = Field(default=None, pattern=r"^\d+-\d+$")
    oldest_cursor: str | None = Field(default=None, pattern=r"^\d+-\d+$")
    earliest_cursor: str | None = Field(default=None, pattern=r"^\d+-\d+$")
    has_more: bool
    scan: OperationalEventScan


class OperationalEventCursorReset(BaseModel):
    """Typed resume instruction returned after retention trims a cursor."""

    model_config = ConfigDict(frozen=True)

    code: str = "operational_event_cursor_trimmed"
    earliest_cursor: str
    latest_cursor: str
