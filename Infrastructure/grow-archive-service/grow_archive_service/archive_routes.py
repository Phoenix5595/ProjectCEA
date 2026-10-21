"""Archive Historical API routes; GET handlers never perform Files network I/O."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime
from starlette.background import BackgroundTask

from grow_archive_service.api_models import (
    ArchiveDetail, ArchiveEntry, ArchiveGraphResponse, ArchiveJobSummary, ArchiveList,
    ExpectedRevision, ExportQueued, ExportReady, LoadQueued, LoadReady,
)
from grow_archive_service.graphs import ArchiveGraphReader
from grow_archive_service.loads import job_summary, load_result_from_job, request_export, request_load
from grow_archive_service.models import ArchiveError
from grow_archive_service.repository import ArchiveRepository

router = APIRouter(prefix='/api/archives', tags=['archives'])


def _repository(request: Request) -> ArchiveRepository:
    pool = getattr(request.app.state.resources, 'pool', None)
    if pool is None:
        raise ArchiveError('archive_database_unavailable', status_code=503)
    return ArchiveRepository(pool)


def _export_service(request: Request) -> Any:
    service = getattr(request.app.state.resources, 'export_service', None)
    if service is None:
        raise ArchiveError('archive_files_unavailable', status_code=503)
    return service


def _job_queue(request: Request) -> Any:
    queue = getattr(request.app.state.resources, 'queue', None)
    if queue is None:
        raise ArchiveError('archive_database_unavailable', status_code=503)
    return queue


@router.get('', response_model=ArchiveList)
async def list_archives(
    request: Request,
    room: str = Query(...),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> ArchiveList:
    page = await _repository(request).list_entries(room, cursor, limit)
    return ArchiveList(items=tuple(ArchiveEntry.model_validate(item) for item in page.items),
                       next_cursor=page.next_cursor)


@router.get('/{dataset_id}', response_model=ArchiveDetail)
async def archive_detail(dataset_id: UUID, request: Request) -> ArchiveDetail:
    detail = await _repository(request).detail(dataset_id)
    return ArchiveDetail.model_validate(detail)


@router.post('/{dataset_id}/exports', response_model=ExportQueued | ExportReady,
             responses={200: {'model': ExportReady}, 202: {'model': ExportQueued}})
async def export_archive(dataset_id: UUID, body: ExpectedRevision,
                         request: Request, response: Response) -> ExportQueued | ExportReady:
    result = await request_export(_export_service(request), _repository(request),
                                  dataset_id, body.expected_revision)
    response.status_code = 200 if isinstance(result, ExportReady) else 202
    return result


@router.post('/{dataset_id}/loads', response_model=LoadQueued | LoadReady,
             responses={200: {'model': LoadReady}, 202: {'model': LoadQueued}})
async def load_archive(dataset_id: UUID, body: ExpectedRevision,
                       request: Request, response: Response) -> LoadQueued | LoadReady:
    result = await request_load(_export_service(request), _repository(request),
                                dataset_id, body.expected_revision)
    response.status_code = 200 if isinstance(result, LoadReady) else 202
    return result


@router.delete('/loads/{load_id}', status_code=204)
async def release_archive_load(load_id: UUID, request: Request) -> Response:
    await _export_service(request).release_load(load_id)
    return Response(status_code=204)


@router.get('/jobs/{job_id}', response_model=ArchiveJobSummary)
async def archive_job(job_id: UUID, request: Request) -> ArchiveJobSummary:
    job = await _job_queue(request).get(job_id)
    value = job_summary(job)
    if job.kind == 'load' and job.state == 'succeeded':
        progress = await load_result_from_job(_export_service(request), _repository(request), job)
        return value.model_copy(update={'progress': progress})
    return value


@router.post('/jobs/{job_id}/retry', response_model=ArchiveJobSummary)
async def retry_archive_job(job_id: UUID, request: Request) -> ArchiveJobSummary:
    job = await _job_queue(request).retry(job_id)
    return job_summary(job)


@router.get('/{dataset_id}/graph', response_model=ArchiveGraphResponse)
async def archive_graph(
    dataset_id: UUID,
    request: Request,
    load_id: UUID = Query(...),
    start: AwareDatetime = Query(...),
    end: AwareDatetime = Query(...),
    max_points: int = Query(default=600, ge=10, le=1000),
) -> ArchiveGraphResponse:
    service = _export_service(request)
    repository = _repository(request)
    resources = request.app.state.resources
    executor = resources.heavy_work
    if executor is None or resources.settings is None:
        raise ArchiveError('archive_worker_unavailable', status_code=503)
    async with service.acquire_load(load_id) as pin:
        if pin.dataset_id != dataset_id:
            raise ArchiveError('archive_load_required')
        result = await ArchiveGraphReader(repository.pool, executor.run,
            resources.settings.archive_root).read(
                pin, start=start, end=end, max_points=max_points)
    return ArchiveGraphResponse.model_validate(result)


@router.get('/{dataset_id}/download')
async def download_archive(
    dataset_id: UUID,
    request: Request,
    load_id: UUID = Query(...),
) -> StreamingResponse:
    service = _export_service(request)
    repository = _repository(request)
    stack = AsyncExitStack()
    try:
        pin = await stack.enter_async_context(service.acquire_load(load_id))
        if pin.dataset_id != dataset_id:
            raise ArchiveError('archive_load_required')
        if pin.source_kind != 'zip' or pin.local_path is None or pin.artifact_id is None:
            raise ArchiveError('archive_export_required')
        path = Path(pin.local_path)
        if path.is_symlink() or not path.is_file():
            raise ArchiveError('archive_integrity_failed')
        download = await repository.download_metadata(pin.artifact_id, dataset_id, int(pin.revision))
        filename = download.filename
        label = download.label

        async def stream() -> AsyncIterator[bytes]:
            try:
                with path.open('rb') as source:
                    while block := await asyncio.to_thread(source.read, 1024 * 1024):
                        yield block
            finally:
                await stack.aclose()

        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"',
            'X-Archive-Label': str(label),
        }
        return StreamingResponse(stream(), media_type='application/zip', headers=headers,
                                 background=BackgroundTask(stack.aclose))
    except BaseException:
        await stack.aclose()
        raise
