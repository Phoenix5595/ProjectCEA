"""Archive-specific errors and immutable job contracts; no resource initialization."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping
from uuid import UUID

JobKind = Literal['export', 'annual_merge', 'load', 'expire', 'artifact_cleanup']
JobState = Literal['pending', 'running', 'succeeded', 'blocked', 'failed']


class ArchiveError(Exception):
    def __init__(self, code: str, *, status_code: int = 409, detail: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = dict(detail or {})
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class JobFence:
    dataset_id: UUID
    data_revision: int
    calendar_revision: int
    required_closed: bool


@dataclass(frozen=True, slots=True)
class ArchiveJob:
    job_id: UUID
    dataset_id: UUID
    kind: JobKind
    requested_revision: int
    state: JobState
    checkpoint: Mapping[str, Any]
    attempts: int
    next_attempt_at: datetime
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobClaim:
    job: ArchiveJob
    owner: UUID
    token: UUID
    lease_until: datetime
    fence: JobFence
