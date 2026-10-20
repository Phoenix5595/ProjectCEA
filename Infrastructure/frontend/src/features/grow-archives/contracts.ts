/**
 * Runtime-validated wire contracts for the selectable Historical archive log.
 *
 * All archive reads cross this boundary as validated data. Instants are parsed
 * once into Date objects; ZIP bytes are never carried in these contracts.
 */
import { z } from 'zod'

import {
  MonitoringResponse,
} from '../monitoring/api'
import {
  HistoryOverviewControlSeries,
  HistoryOverviewSensorResponse,
} from '../history-overview/contracts'

const archiveInstant = z
  .string()
  .refine(value => Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value), {
    message: 'archive timestamp must include an explicit UTC offset',
  })
  .transform(value => new Date(value))

export const ArchiveRoom = z.enum(['Flower Room', 'Veg Room'])
export type ArchiveRoom = z.infer<typeof ArchiveRoom>

export const ArchiveKind = z.enum(['grow', 'veg_quarter', 'veg_year'])
export type ArchiveKind = z.infer<typeof ArchiveKind>

export const ArchiveState = z.enum(['pending', 'copying', 'ready', 'dirty', 'blocked', 'zip_only'])
export type ArchiveState = z.infer<typeof ArchiveState>

export const ArchiveLocalTier = z.enum(['hot', 'zip_cache', 'remote_zip', 'none'])
export type ArchiveLocalTier = z.infer<typeof ArchiveLocalTier>

export const ArchiveExportStatus = z.enum(['not_exported', 'pending', 'verified', 'blocked'])
export type ArchiveExportStatus = z.infer<typeof ArchiveExportStatus>

export const ArchiveCoverage = z.enum(['complete', 'gapped', 'unknown'])
export type ArchiveCoverage = z.infer<typeof ArchiveCoverage>

export const ArchiveJobState = z.enum(['pending', 'running', 'succeeded', 'blocked', 'failed'])
export type ArchiveJobState = z.infer<typeof ArchiveJobState>

export const ArchiveJobKind = z.enum(['export', 'annual_merge', 'load', 'expire', 'artifact_cleanup'])
export type ArchiveJobKind = z.infer<typeof ArchiveJobKind>

export const ArchiveGapInterval = z
  .object({
    start: archiveInstant,
    end: archiveInstant,
    reason: z.string(),
    source_tables: z.array(z.string()),
  })
  .strict()
export type ArchiveGapInterval = z.infer<typeof ArchiveGapInterval>

export const ArchiveJobSummary = z
  .object({
    job_id: z.string().uuid(),
    kind: ArchiveJobKind,
    state: ArchiveJobState,
    requested_revision: z.number().int().nonnegative(),
    progress: z.record(z.string(), z.unknown()),
    last_error_code: z.string().nullable(),
    attempts: z.number().int().nonnegative(),
    next_attempt_at: archiveInstant.nullable(),
    updated_at: archiveInstant,
  })
  .passthrough()
export type ArchiveJobSummary = z.infer<typeof ArchiveJobSummary>

const ArchiveContainer = z
  .object({
    dataset_id: z.string().uuid(),
    revision: z.number().int().nonnegative(),
    kind: z.literal('veg_year'),
    remote_download_url: z.string().nullable(),
  })
  .passthrough()
export type ArchiveContainer = z.infer<typeof ArchiveContainer>

export const ArchiveEntry = z
  .object({
    dataset_id: z.string().uuid(),
    kind: ArchiveKind,
    name: z.string(),
    room: ArchiveRoom,
    start: archiveInstant.nullable(),
    end: archiveInstant.nullable(),
    state: ArchiveState,
    revision: z.number().int().nonnegative(),
    published_revision: z.number().int().nonnegative().nullable(),
    lifecycle_state: z.enum(['open', 'closed']).nullable(),
    anniversary_at: archiveInstant.nullable(),
    local_tier: ArchiveLocalTier,
    export_status: ArchiveExportStatus,
    zip_bytes: z.number().int().nonnegative().nullable(),
    coverage: ArchiveCoverage,
    gap_intervals: z.array(ArchiveGapInterval),
    job: ArchiveJobSummary.nullable(),
    remote_download_url: z.string().nullable(),
    download_label: z.string().nullable(),
    container: ArchiveContainer.nullable(),
  })
  .passthrough()
export type ArchiveEntry = z.infer<typeof ArchiveEntry>

export const ArchiveEntryPage = z
  .object({
    items: z.array(ArchiveEntry),
    next_cursor: z.string().nullable(),
  })
  .strict()
export type ArchiveEntryPage = z.infer<typeof ArchiveEntryPage>

export const ArchivePhase = z
  .object({
    event_id: z.number().int(),
    phase_type: z.string(),
    room: ArchiveRoom,
    start_at: archiveInstant.nullable(),
    end_at: archiveInstant.nullable(),
    start_date: z.string().nullable(),
    end_date: z.string().nullable(),
  })
  .passthrough()
export type ArchivePhase = z.infer<typeof ArchivePhase>

export const ArchiveAvailability = z
  .object({
    source_table: z.string(),
    room: z.string().nullable(),
    start_at: archiveInstant,
    end_at: archiveInstant,
    state: z.enum(['captured', 'expired']),
    source_rows_removed: z.boolean(),
    aggregates_refreshed: z.boolean(),
  })
  .passthrough()
export type ArchiveAvailability = z.infer<typeof ArchiveAvailability>

export const ArchiveIdentitySnapshot = z.record(z.string(), z.unknown())
export type ArchiveIdentitySnapshot = z.infer<typeof ArchiveIdentitySnapshot>

export const ArchiveDetail = ArchiveEntry.extend({
  phases: z.array(ArchivePhase),
  schema: z
    .object({
      source_schema: z.record(z.string(), z.unknown()),
      graph_schema: z.record(z.string(), z.unknown()),
    })
    .passthrough(),
  availability: z.array(ArchiveAvailability),
  identity_snapshots: z.array(ArchiveIdentitySnapshot),
})
export type ArchiveDetail = z.infer<typeof ArchiveDetail>

export const ArchiveExportResponse = z.discriminatedUnion('state', [
  z
    .object({
      artifact_id: z.string().uuid(),
      revision: z.number().int().nonnegative(),
      state: z.literal('verified'),
    })
    .passthrough(),
  z
    .object({
      job_id: z.string().uuid(),
      state: z.literal('pending'),
    })
    .passthrough(),
])
export type ArchiveExportResponse = z.infer<typeof ArchiveExportResponse>

export const ArchiveReadyLoad = z.object({
  load_id: z.string().uuid(),
  revision: z.number().int().nonnegative(),
  state: z.literal('ready'),
  source_kind: z.literal('zip'),
  stale: z.boolean(),
  remote_download_url: z.string().url().nullable(),
  download_label: z.string().nullable(),
}).passthrough()
export type ArchiveReadyLoad = z.infer<typeof ArchiveReadyLoad>

export const ArchiveLoadResponse = z.discriminatedUnion('state', [
  ArchiveReadyLoad,
  z
    .object({
      job_id: z.string().uuid(),
      state: z.literal('pending'),
    })
    .passthrough(),
])
export type ArchiveLoadResponse = z.infer<typeof ArchiveLoadResponse>

export const ArchiveJob = z
  .object({
    job_id: z.string().uuid(),
    dataset_id: z.string().uuid(),
    kind: ArchiveJobKind,
    requested_revision: z.number().int().nonnegative(),
    state: ArchiveJobState,
    progress: z.record(z.string(), z.unknown()),
    last_error_code: z.string().nullable(),
    attempts: z.number().int().nonnegative(),
    next_attempt_at: archiveInstant.nullable(),
    created_at: archiveInstant,
    updated_at: archiveInstant,
  })
  .passthrough()
export type ArchiveJob = z.infer<typeof ArchiveJob>

export const ArchivePhotoperiodFact = z
  .object({
    id: z.union([z.string(), z.number().int()]),
    time: archiveInstant,
    phase: z.string(),
    mode_id: z.number().int().nullable(),
    submode_id: z.number().int().nullable(),
    runtime_snapshot_version: z.number().int().nullable(),
    source: z.string(),
  })
  .passthrough()
export type ArchivePhotoperiodFact = z.infer<typeof ArchivePhotoperiodFact>

export const ArchivePhotoperiodCoverageFact = z
  .object({
    id: z.union([z.string(), z.number().int()]),
    time: archiveInstant,
    state: z.string(),
    reason: z.string().nullable(),
    runtime_snapshot_version: z.number().int().nullable(),
  })
  .passthrough()
export type ArchivePhotoperiodCoverageFact = z.infer<typeof ArchivePhotoperiodCoverageFact>

const ArchiveGraphFields = {
  archive: z
    .object({
      dataset_id: z.string().uuid(),
      revision: z.number().int().positive(),
      coverage: ArchiveCoverage,
      gaps: z.array(ArchiveGapInterval),
    })
    .strict(),
  lights: z.array(HistoryOverviewControlSeries),
  photoperiod: z.array(ArchivePhotoperiodFact),
  photoperiod_coverage: z.array(ArchivePhotoperiodCoverageFact),
}

export const ArchiveGraph = z.discriminatedUnion('tier', [
  z.object({ ...ArchiveGraphFields, tier: z.literal('detailed'), sensors: MonitoringResponse }).strict(),
  z.object({ ...ArchiveGraphFields, tier: z.literal('overview'), sensors: HistoryOverviewSensorResponse }).strict(),
])
export type ArchiveGraph = z.infer<typeof ArchiveGraph>
