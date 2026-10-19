"""Strict public DTOs for Historical archive selection and graph reads."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StrictStr
from monitoring_service.history_overview_models import (
    HistoryOverviewControlSeries,
    HistoryOverviewSensorResponse,
)
from monitoring_service.sensor_models import MonitoringResponse

ArchiveKind = Literal['grow', 'veg_quarter', 'veg_year']
JobKind = Literal['export', 'annual_merge', 'load', 'expire', 'artifact_cleanup']
JobState = Literal['pending', 'running', 'succeeded', 'blocked', 'failed']
LocalTier = Literal['hot', 'zip_cache', 'remote_zip', 'none']
ExportStatus = Literal['not_exported', 'pending', 'verified', 'blocked']
CoverageState = Literal['complete', 'gapped', 'unknown']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class ExpectedRevision(StrictModel):
    expected_revision: StrictInt = Field(gt=0)


class ArchiveGap(StrictModel):
    start: AwareDatetime
    end: AwareDatetime
    reason: StrictStr
    source_tables: tuple[StrictStr, ...] = ()


class ArchiveJobSummary(StrictModel):
    job_id: UUID
    dataset_id: UUID
    kind: JobKind
    requested_revision: StrictInt
    state: JobState
    progress: dict[str, Any] = Field(default_factory=dict)
    last_error_code: StrictStr | None = None
    attempts: StrictInt = Field(ge=0)
    next_attempt_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ArchiveContainer(StrictModel):
    dataset_id: UUID
    revision: StrictInt
    kind: Literal['veg_year']
    remote_download_url: StrictStr | None = None


class ArchiveEntry(StrictModel):
    dataset_id: UUID
    kind: ArchiveKind
    name: StrictStr
    room: StrictStr
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    state: StrictStr
    revision: StrictInt
    published_revision: StrictInt | None = None
    lifecycle_state: Literal['open', 'closed'] | None = None
    anniversary_at: AwareDatetime | None = None
    local_tier: LocalTier
    export_status: ExportStatus
    zip_bytes: StrictInt | None = Field(default=None, ge=0)
    coverage: CoverageState
    gap_intervals: tuple[ArchiveGap, ...] = ()
    job: ArchiveJobSummary | None = None
    remote_download_url: StrictStr | None = None
    download_label: StrictStr | None = None
    container: ArchiveContainer | None = None


class ArchiveList(StrictModel):
    items: tuple[ArchiveEntry, ...]
    next_cursor: StrictStr | None = None


class GrowPhase(StrictModel):
    event_id: StrictInt
    phase_type: StrictStr
    room: StrictStr
    start_at: AwareDatetime
    end_at: AwareDatetime
    start_date: StrictStr
    end_date: StrictStr | None = None


class ArchiveAvailability(StrictModel):
    source_table: StrictStr
    room: StrictStr | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime
    state: Literal['captured', 'expired']
    source_rows_removed: bool
    aggregates_refreshed: bool


class ArchiveSchema(StrictModel):
    source_schema: dict[str, Any] = Field(default_factory=dict)
    graph_schema: dict[str, Any] = Field(default_factory=dict)



class ArchiveDetail(ArchiveEntry):
    phases: tuple[GrowPhase, ...] = ()
    schema_info: ArchiveSchema = Field(alias='schema')
    availability: tuple[ArchiveAvailability, ...] = ()
    identity_snapshots: tuple[dict[str, Any], ...] = ()


class ExportQueued(StrictModel):
    job_id: UUID
    state: Literal['pending'] = 'pending'


class ExportReady(StrictModel):
    artifact_id: UUID
    revision: StrictInt
    state: Literal['verified'] = 'verified'


class LoadQueued(StrictModel):
    job_id: UUID
    state: Literal['pending'] = 'pending'


class LoadReady(StrictModel):
    load_id: UUID
    revision: StrictInt
    state: Literal['ready'] = 'ready'
    source_kind: Literal['zip']
    stale: bool
    remote_download_url: StrictStr | None
    download_label: StrictStr | None


class ArchiveSelectionMeta(StrictModel):
    dataset_id: UUID
    revision: StrictInt
    coverage: CoverageState
    gaps: tuple[ArchiveGap, ...] = ()


class PhotoperiodFact(StrictModel):
    id: StrictInt
    observed_at: AwareDatetime
    phase: StrictStr
    mode_id: StrictInt | None = None
    submode_id: StrictInt | None = None
    runtime_snapshot_version: StrictInt
    source: StrictStr


class PhotoperiodCoverageFact(StrictModel):
    id: StrictInt
    observed_at: AwareDatetime
    state: StrictStr
    reason: StrictStr
    runtime_snapshot_version: StrictInt


class ArchiveGraphResponse(StrictModel):
    archive: ArchiveSelectionMeta
    tier: Literal['detailed', 'overview']
    sensors: MonitoringResponse | HistoryOverviewSensorResponse
    lights: tuple[HistoryOverviewControlSeries, ...]
    photoperiod: tuple[PhotoperiodFact, ...]
    photoperiod_coverage: tuple[PhotoperiodCoverageFact, ...]
