"""Explicit archive export/load operations over the Files pipeline."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from uuid import UUID

from grow_archive_service.api_models import (
    ArchiveJobSummary, ExportQueued, ExportReady, LoadQueued, LoadReady,
)
from grow_archive_service.models import ArchiveError, ArchiveJob
from grow_archive_service.repository import ArchiveRepository


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


_PRIVATE_PROGRESS_KEY = re.compile(r'(?:secret|password|credential|authorization|lease.?token|token|url|path|dsn|raw|payload|manifest)', re.I)


def _public_progress(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): clean(child) for key, child in item.items()
                    if not _PRIVATE_PROGRESS_KEY.search(str(key))}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if item is None or isinstance(item, (str, int, float, bool, UUID)):
            return str(item) if isinstance(item, UUID) else item
        return None
    return clean(value)


def job_summary(job: ArchiveJob) -> ArchiveJobSummary:
    checkpoint = dict(job.checkpoint)
    progress = checkpoint.get('progress')
    return ArchiveJobSummary(
        job_id=job.job_id, dataset_id=job.dataset_id, kind=job.kind,
        requested_revision=job.requested_revision, state=job.state,
        progress=_public_progress(progress),
        last_error_code=job.last_error_code, attempts=job.attempts,
        next_attempt_at=job.next_attempt_at, created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def request_export(service: Any, repository: ArchiveRepository,
                         dataset_id: UUID, expected_revision: int) -> ExportQueued | ExportReady:
    await repository.export_preflight(dataset_id, expected_revision)
    result = await service.request_export(dataset_id, expected_revision)
    artifact_id = _field(result, 'artifact_id')
    if artifact_id is not None:
        revision = _field(result, 'revision', expected_revision)
        if int(revision) != expected_revision:
            raise ArchiveError('archive_integrity_failed')
        return ExportReady(artifact_id=artifact_id, revision=revision)
    job_id = _field(result, 'job_id')
    if job_id is None:
        raise ArchiveError('archive_job_unavailable', status_code=503)
    return ExportQueued(job_id=job_id)


async def request_load(service: Any, repository: ArchiveRepository,
                       dataset_id: UUID, requested_revision: int) -> LoadQueued | LoadReady:
    context = await repository.graph_context(dataset_id)
    current_revision = int(context['data_revision'])
    published = context.get('published_revision')
    if requested_revision > current_revision:
        raise ArchiveError('archive_revision_conflict')
    if published is None:
        raise ArchiveError('archive_export_required')
    if requested_revision == current_revision and requested_revision > int(published):
        raise ArchiveError('archive_revision_pending')
    if requested_revision != int(published):
        raise ArchiveError('archive_revision_conflict')
    result = await service.enqueue_load(dataset_id, requested_revision)
    pin = _field(result, 'load')
    if pin is not None:
        revision = int(_field(pin, 'revision'))
        if revision != requested_revision:
            raise ArchiveError('archive_integrity_failed')
        source_kind = _field(pin, 'source_kind')
        if source_kind != 'zip':
            raise ArchiveError('archive_integrity_failed')
        download = await repository.load_download_metadata(
            _field(pin, 'artifact_id'), dataset_id, revision)
        return LoadReady(load_id=_field(pin, 'load_id'), revision=revision,
                         source_kind=source_kind, stale=revision != current_revision, **download)
    job_id = _field(result, 'job_id')
    if job_id is None:
        raise ArchiveError('archive_job_unavailable', status_code=503)
    return LoadQueued(job_id=job_id)


async def load_result_from_job(service: Any, repository: ArchiveRepository, job: ArchiveJob) -> dict[str, Any]:
    """Expose the completed explicit selection from its durable job checkpoint."""
    progress = job.checkpoint.get('progress', {})
    if not isinstance(progress, Mapping):
        progress = {}
    if job.kind != 'load' or job.state != 'succeeded':
        return dict(progress)
    load_id = progress.get('load_id')
    if load_id is None:
        raise ArchiveError('archive_integrity_failed')
    try:
        pin = await service.load_pin(UUID(str(load_id)))
    except (ValueError, TypeError) as error:
        raise ArchiveError('archive_integrity_failed') from error
    if int(_field(pin, 'revision', -1)) != int(progress.get('revision', -2)):
        raise ArchiveError('archive_integrity_failed')
    context = await repository.graph_context(job.dataset_id)
    revision = int(_field(pin, 'revision'))
    download = await repository.load_download_metadata(
        _field(pin, 'artifact_id'), job.dataset_id, revision)
    # Trusted catalog links are not arbitrary checkpoint progress or secrets.
    return {**_public_progress({**dict(progress), 'load_id': str(_field(pin, 'load_id')),
            'revision': revision, 'source_kind': _field(pin, 'source_kind'),
            'stale': revision != int(context['data_revision'])}), **download}
