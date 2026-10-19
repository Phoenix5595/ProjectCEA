"""Authenticated archive API; imports do not start resources or Arrow."""
from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from grow_archive_service.models import ArchiveError
from grow_archive_service.repository import ArchiveRepository

router = APIRouter(prefix='/api/archives', tags=['archives'])


class ScopeAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: UUID | None = None
    expected_revision: StrictInt | None = Field(default=None, gt=0)
    source_table: Literal['measurement']
    source_identity: dict[str, StrictInt]
    start: AwareDatetime
    end: AwareDatetime
    room: str


class ScopeAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    override_id: int
    source_table: Literal['measurement']
    source_identity: dict[str,int]
    start: datetime
    end: datetime
    room: str
    confirmed_at: datetime
    affected_dataset_ids: tuple[UUID,...]


def get_repository(request: Request) -> ArchiveRepository:
    pool = request.app.state.resources.pool
    if pool is None:
        raise ArchiveError('archive_database_unavailable',status_code=503)
    return ArchiveRepository(pool)


@router.post('/scope-corrections',response_model=ScopeAssignmentResponse)
async def confirm_scope_assignment(body: ScopeAssignmentRequest,
                                   repository: ArchiveRepository = Depends(get_repository)) -> ScopeAssignmentResponse:
    correction = await repository.confirm_scope_assignment(**body.model_dump())
    return ScopeAssignmentResponse(**asdict(correction))
