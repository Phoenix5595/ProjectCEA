/**
 * In-memory archive-service fixtures used only by vite.monitoring.config.ts.
 *
 * The preview middleware calls this handler only on its guarded 127.0.0.1
 * origin and only for explicit `archive-*` scenarios. Unmatched archive paths
 * remain denied by the middleware; no fixture contacts a database, DAV host,
 * or real archive service.
 */
import { historyOverviewBucketBounds } from '../history-overview/buckets.ts'

interface FixtureRequest {
  readonly url?: string
  readonly method?: string
  readonly body?: string
}

export interface ArchiveFixtureReply {
  readonly status: number
  readonly body: unknown
}

interface FixtureJob {
  readonly datasetId: string
  readonly action: 'export' | 'load'
  polls: number
  state: 'pending' | 'running' | 'succeeded' | 'failed' | 'blocked'
  readonly loadId?: string
  readonly revision: number
  readonly remoteDownloadUrl?: string | null
  readonly downloadLabel?: string | null
}

const ALLOWED_SCENARIOS = new Set([
  'archive-ui',
  'archive-ready',
  'archive-gapped',
  'archive-empty',
  'archive-zip-only',
  'archive-job-progress',
  'archive-unavailable',
  'archive-load-job',
  'archive-revision-conflict',
  'archive-many-facts',
])
const FIXTURE_SESSION_FALLBACK = 'missing-session'
const FIXTURE_JOBS = new Map<string, FixtureJob>()
const BASE_START_MS = Date.UTC(2026, 6, 1)
const DAY_MS = 24 * 60 * 60 * 1000
const GAP_START = Date.UTC(2026, 6, 9)
const GAP_END = Date.UTC(2026, 6, 10)

function uuid(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`
}

function datasetOrdinal(datasetId: string): number {
  return Number(datasetId.slice(-12))
}

function iso(value: number): string {
  return new Date(value).toISOString()
}

function fileUrl(origin: string, name: string): string {
  return `${origin}/fixture-nextcloud/SJCEA/Grow%20Archives/${encodeURIComponent(name)}`
}

function fixtureJob(
  session: string,
  id: string,
  datasetId: string,
  action: 'export' | 'load',
  revision: number,
  initialState: FixtureJob['state'] = 'running'
): FixtureJob {
  const key = `${session}:${id}`
  const existing = FIXTURE_JOBS.get(key)
  if (existing !== undefined) return existing
  const job: FixtureJob = { datasetId, action, polls: 0, state: initialState, revision }
  FIXTURE_JOBS.set(key, job)
  return job
}

function gapIntervals(): Array<Record<string, unknown>> {
  return [{
    start: iso(GAP_START),
    end: iso(GAP_END),
    reason: 'Recorded source coverage was unavailable in this isolated preview fixture.',
    source_tables: ['measurement'],
  }]
}

function makeEntry(index: number, origin: string, room: 'Flower Room' | 'Veg Room'): Record<string, unknown> {
  const datasetId = uuid(index + (room === 'Flower Room' ? 1 : 101))
  const endMs = BASE_START_MS + (14 - index) * DAY_MS
  const startMs = endMs - 14 * DAY_MS
  const vegQuarter = room === 'Veg Room'
  const kind = vegQuarter ? 'veg_quarter' : 'grow'
  const gapped = index === 1
  const zipOnly = index === 2
  const pending = index === 3
  const blocked = index === 4
  const retryable = index === 6
  const revision = gapped ? 3 : 2
  const publishedRevision = gapped ? 2 : (blocked || retryable ? null : revision)
  const annualUrl = fileUrl(origin, '2026-r2-fixture-annual.zip')
  const remoteUrl = zipOnly
    ? fileUrl(origin, `${datasetId}-r${revision}-fixture.zip`)
    : gapped && vegQuarter
      ? annualUrl
      : (!pending && !blocked ? fileUrl(origin, `${datasetId}-r${revision}-fixture.zip`) : null)
  const state = pending ? 'copying' : blocked || retryable ? 'blocked' : zipOnly ? 'zip_only' : (gapped ? 'dirty' : 'ready')
  const jobId = uuid(800 + index + (room === 'Flower Room' ? 0 : 20))
  const job = pending
    ? {
        job_id: jobId,
        kind: 'export',
        state: 'running',
        requested_revision: revision,
        progress: { phase: 'upload', completed: 3, total: 5, percent: 60 },
        last_error_code: null,
        attempts: 1,
        next_attempt_at: null,
        updated_at: iso(endMs),
      }
    : blocked
      ? {
          job_id: jobId,
          kind: 'export',
          state: 'blocked',
          requested_revision: revision,
          progress: { phase: 'verification' },
          last_error_code: 'archive_integrity_failed',
          attempts: 1,
          next_attempt_at: null,
          updated_at: iso(endMs),
        }
      : retryable
        ? {
            job_id: jobId,
            kind: 'export',
            state: 'failed',
            requested_revision: revision,
            progress: { phase: 'upload' },
            last_error_code: 'archive_remote_unavailable',
            attempts: 2,
            next_attempt_at: null,
            updated_at: iso(endMs),
          }
        : null
  const container = gapped && vegQuarter
    ? {
        dataset_id: uuid(900),
        revision: 2,
        kind: 'veg_year',
        remote_download_url: annualUrl,
      }
    : null
  const downloadLabel = container === null
    ? `Open verified ${vegQuarter ? 'Veg period' : 'grow'} ZIP in Nextcloud`
    : 'Download 2026 ZIP (all four quarters)'

  return {
    dataset_id: datasetId,
    kind,
    name: vegQuarter ? `Veg ${2026 - Math.floor(index / 4)} Q${(index % 4) + 1}` : `Flower grow ${index + 1}`,
    room,
    start: iso(startMs),
    end: iso(endMs),
    state,
    revision,
    published_revision: publishedRevision,
    lifecycle_state: vegQuarter ? null : (index === 5 ? 'open' : 'closed'),
    anniversary_at: vegQuarter ? null : iso(endMs + 365 * DAY_MS),
    local_tier: zipOnly ? 'remote_zip' : (pending ? 'hot' : (blocked ? 'none' : 'zip_cache')),
    export_status: pending ? 'pending' : (blocked ? 'blocked' : (publishedRevision === null ? 'not_exported' : 'verified')),
    zip_bytes: zipOnly ? 5 * 1024 ** 3 : (publishedRevision === null ? null : 13_402_112 + index * 147_456),
    coverage: gapped ? 'gapped' : (blocked ? 'unknown' : 'complete'),
    gap_intervals: gapped ? gapIntervals() : [],
    job,
    remote_download_url: publishedRevision === null ? null : remoteUrl,
    download_label: publishedRevision === null ? null : downloadLabel,
    container,
  }
}

function entriesFor(room: 'Flower Room' | 'Veg Room', scenario: string, origin: string): Array<Record<string, unknown>> {
  if (scenario === 'archive-empty') return []
  if (scenario === 'archive-gapped') return room === 'Veg Room' ? [makeEntry(1, origin, room)] : []
  if (scenario === 'archive-zip-only') return room === 'Flower Room' ? [makeEntry(2, origin, room)] : []
  if (scenario === 'archive-job-progress') return room === 'Flower Room' ? [makeEntry(3, origin, room)] : []
  if (scenario === 'archive-revision-conflict') {
    if (room !== 'Flower Room') return []
    const reopened = makeEntry(5, origin, room)
    return [{
      ...reopened,
      state: 'dirty',
      revision: 3,
      published_revision: 2,
      lifecycle_state: 'open',
      export_status: 'verified',
      job: {
        job_id: uuid(805), kind: 'export', state: 'blocked', requested_revision: 3,
        progress: { phase: 'fence_check' }, last_error_code: 'archive_job_superseded',
        attempts: 1, next_attempt_at: null, updated_at: '2026-10-01T00:00:00.000Z',
      },
    }]
  }
  if (scenario === 'archive-ready') return [makeEntry(0, origin, room)]
  return Array.from({ length: 12 }, (_, index) => makeEntry(index, origin, room))
}

function entryById(datasetId: string, origin: string, session: string, scenario: string): Record<string, unknown> | null {
  for (const room of ['Flower Room', 'Veg Room'] as const) {
    for (let index = 0; index < 12; index += 1) {
      const entry = makeEntry(index, origin, room)
      if (entry.dataset_id === datasetId) {
        const value = scenario === 'archive-revision-conflict' && datasetId === uuid(6)
          ? { ...entry, state: 'dirty', revision: 3, published_revision: 2, lifecycle_state: 'open', export_status: 'verified', job: {
              job_id: uuid(805), kind: 'export', state: 'blocked', requested_revision: 3,
              progress: { phase: 'fence_check' }, last_error_code: 'archive_job_superseded',
              attempts: 1, next_attempt_at: null, updated_at: '2026-10-01T00:00:00.000Z',
            } }
          : entry
        const job = value.job as { job_id?: string; state?: FixtureJob['state'] } | null
        if (job?.job_id !== undefined) fixtureJob(session, job.job_id, datasetId, 'export', value.revision as number, job.state)
        return value
      }
    }
  }
  return null
}

function archiveDetail(entry: Record<string, unknown>, scenario: string): Record<string, unknown> {
  const room = entry.room as 'Flower Room' | 'Veg Room'
  const start = entry.start as string
  const end = entry.end as string
  const gap = entry.coverage === 'gapped' ? gapIntervals() : []
  return {
    ...entry,
    phases: room === 'Flower Room'
      ? [
          { event_id: 7001, phase_type: 'bed_veg', room, start_at: start, end_at: iso(Date.parse(start) + 7 * DAY_MS), start_date: '2026-07-01', end_date: '2026-07-07' },
          { event_id: 7002, phase_type: 'flower', room, start_at: iso(Date.parse(start) + 7 * DAY_MS), end_at: end, start_date: '2026-07-08', end_date: '2026-07-21' },
        ]
      : [],
    schema: {
      source_schema: { measurement: [{ name: 'time', type: 'timestamptz' }, { name: 'sensor_id', type: 'integer' }, { name: 'value', type: 'real' }] },
      graph_schema: { sensor: ['bucket_start', 'sample_count', 'minimum', 'maximum'], light: ['bucket_start', 'sample_count', 'minimum', 'maximum'] },
    },
    availability: [{ source_table: 'measurement', room, start_at: start, end_at: end, state: 'captured', source_rows_removed: false, aggregates_refreshed: true }],
    identity_snapshots: scenario === 'archive-many-facts'
      ? Array.from({ length: 12 }, (_, index) => ({
          source_table: 'sensor', source_identity: { sensor_id: index + 1 },
          snapshot: { name: `fixture_capteur_${index + 1}`, room, node: index % 2 === 0 ? 'front' : 'back',
            calibration: { gain: 1.000001, offset: -0.000001, description: 'étalonnage conservé' } },
        }))
      : [],
    ...(gap.length === 0 ? {} : { gap_intervals: gap }),
  }
}

function graphReply(url: URL, entry: Record<string, unknown>, scenario: string): Record<string, unknown> {
  const start = new Date(url.searchParams.get('start') ?? '')
  const end = new Date(url.searchParams.get('end') ?? '')
  const bounds = historyOverviewBucketBounds(start, end, 1000)
  const room = entry.room as 'Flower Room' | 'Veg Room'
  const nodes = room === 'Flower Room' ? ['front', 'back'] : ['main']
  const baseNames = room === 'Flower Room'
    ? [{ sensor: 'dry_bulb_f', node: 'front', unit: '°C', unit_family: 'celsius', base: 22.4 }, { sensor: 'rh_f', node: 'front', unit: '%', unit_family: 'percent', base: 61 }]
    : [{ sensor: 'dry_bulb', node: 'main', unit: '°C', unit_family: 'celsius', base: 21.8 }, { sensor: 'rh', node: 'main', unit: '%', unit_family: 'percent', base: 58 }]
  const names = scenario === 'archive-many-facts' && room === 'Flower Room'
    ? [...baseNames,
        { sensor: 'wet_bulb_f', node: 'front', unit: '°C', unit_family: 'celsius', base: 19.8 },
        { sensor: 'dry_bulb_b', node: 'back', unit: '°C', unit_family: 'celsius', base: 23.1 },
        { sensor: 'wet_bulb_b', node: 'back', unit: '°C', unit_family: 'celsius', base: 20.2 },
        { sensor: 'rh_b', node: 'back', unit: '%', unit_family: 'percent', base: 62.5 },
        { sensor: 'co2_f', node: 'front', unit: 'ppm', unit_family: 'ppm', base: 680 },
        { sensor: 'co2_b', node: 'back', unit: 'ppm', unit_family: 'ppm', base: 710 },
      ]
    : baseNames
  const sensorSeries = names.map((identity, seriesIndex) => ({
    sensor: identity.sensor,
    node: identity.node,
    unit: identity.unit,
    unit_family: identity.unit_family,
    points: bounds.buckets.map((bucket, index) => {
      const average = identity.base + ((index + seriesIndex) % 7) * 0.1
      return { start: bucket.start.toISOString(), end: bucket.end.toISOString(), average, minimum: average - 0.4, maximum: average + 0.4, sample_count: 12, partial: bucket.partial }
    }),
  }))
  const statistics = names.map(identity => ({
    sensor: identity.sensor,
    node: identity.node,
    sample_count: bounds.buckets.length * 12,
    minimum: identity.base - 0.4,
    maximum: identity.base + 1.2,
    average: identity.base + 0.4,
    stddev_samp: 0.2,
    stddev_quality: 'exact',
  }))
  const requestedRange = { start: start.toISOString(), end: end.toISOString() }
  const detailed = end.getTime() - start.getTime() <= 7 * DAY_MS
  const detailedPointCount = Math.ceil((end.getTime() - start.getTime()) / (5 * 60 * 1000))
  const detailedSensorSeries = names.map((identity, seriesIndex) => ({
    sensor: identity.sensor,
    node: identity.node,
    unit: identity.unit,
    unit_family: identity.unit_family,
    points: Array.from({ length: detailedPointCount }, (_, index) => {
      const average = identity.base + ((index + seriesIndex) % 7) * 0.1
      return {
        timestamp: iso(start.getTime() + index * 5 * 60 * 1000),
        average,
        minimum: average - 0.4,
        maximum: average + 0.4,
        sample_count: 12,
      }
    }),
    sample_count_total: detailedPointCount * 12,
  }))
  const lightPoints = bounds.buckets.map((bucket, index) => ({
    start: bucket.start.toISOString(),
    end: bucket.end.toISOString(),
    average: index % 12 < 9 ? 80 : 0,
    minimum: index % 12 < 9 ? 80 : 0,
    maximum: index % 12 < 9 ? 80 : 0,
    sample_count: 1,
    partial: bucket.partial,
  }))
  const gapStart = new Date(Math.max(start.getTime(), GAP_START))
  const gapEnd = new Date(Math.min(end.getTime(), GAP_END))
  const gaps = entry.coverage === 'gapped' && gapEnd.getTime() > gapStart.getTime()
    ? [{ start: gapStart.toISOString(), end: gapEnd.toISOString(), reason: 'Fixture source coverage gap', source_tables: ['measurement'] }]
    : []
  const photoperiod = [
    { id: 901, time: start.toISOString(), phase: 'SUN', mode_id: 2, submode_id: null, runtime_snapshot_version: 4, source: 'recorded' },
    { id: 902, time: new Date(Math.min(end.getTime() - 1, start.getTime() + 12 * 60 * 60 * 1000)).toISOString(), phase: 'MOON', mode_id: 2, submode_id: null, runtime_snapshot_version: 4, source: 'recorded' },
  ]
  const photoperiodCoverage = [
    { id: 801, time: start.toISOString(), state: 'covered', reason: null, runtime_snapshot_version: 4 },
  ]
  return {
    archive: { dataset_id: entry.dataset_id, revision: entry.published_revision, coverage: entry.coverage, gaps },
    tier: detailed ? 'detailed' : 'overview',
    sensors: detailed
      ? {
          metadata: {
            generated_at: new Date('2026-10-01T00:00:00.000Z').toISOString(),
            tier: '5min',
            range: requestedRange,
            room: { room, nodes },
            requested_max_points: 1000,
            interval_seconds: 300,
          },
          series: detailedSensorSeries,
          statistics,
        }
      : {
          metadata: {
            generated_at: new Date('2026-10-01T00:00:00.000Z').toISOString(),
            range: requestedRange,
            room: { room, nodes },
            requested_max_points: 1000,
            bucket: bounds.bucket,
            source_coverage: 'ready',
            all_aggregated: true,
          },
          series: sensorSeries,
          statistics,
        },
    lights: [{ location: room, cluster: 'main', device_name: `${room} grow light`, points: lightPoints }],
    photoperiod,
    photoperiod_coverage: photoperiodCoverage,
  }
}

function jobResponse(job: FixtureJob, jobId: string): Record<string, unknown> {
  job.polls += 1
  if (job.polls >= 3) job.state = 'succeeded'
  const progress: Record<string, unknown> = job.action === 'load'
    ? { phase: job.state === 'succeeded' ? 'ready' : 'copying', completed: job.polls, total: 3, percent: Math.min(100, job.polls * 34), ...(job.state === 'succeeded' ? { load_id: job.loadId, revision: job.revision, source_kind: 'zip', stale: false, remote_download_url: job.remoteDownloadUrl ?? null, download_label: job.downloadLabel ?? null } : {}) }
    : { phase: job.state === 'succeeded' ? 'verified' : 'upload', completed: job.polls, total: 3, percent: Math.min(100, job.polls * 34) }
  const now = '2026-10-01T00:00:00.000Z'
  return {
    job_id: jobId,
    dataset_id: job.datasetId,
    kind: job.action,
    requested_revision: job.revision,
    state: job.state,
    progress,
    last_error_code: null,
    attempts: 1,
    next_attempt_at: null,
    created_at: now,
    updated_at: now,
  }
}

/** Return `null` unless this is an explicitly enabled preview scenario. */
export function archiveFixtureReply(
  request: FixtureRequest,
  scenario: string | null,
  fixtureOrigin: string,
  fixtureSession = FIXTURE_SESSION_FALLBACK
): ArchiveFixtureReply | null {
  if (scenario === null || !ALLOWED_SCENARIOS.has(scenario)) return null
  let url: URL
  try {
    url = new URL(request.url ?? '/', fixtureOrigin)
  } catch {
    return null
  }
  if (url.origin !== fixtureOrigin || !url.pathname.startsWith('/api/archives')) return null
  if (scenario === 'archive-unavailable') {
    return { status: 503, body: { detail: { code: 'archive_remote_unavailable' } } }
  }
  const method = (request.method ?? 'GET').toUpperCase()

  if (url.pathname === '/api/archives' && method === 'GET') {
    const room = url.searchParams.get('room')
    if (room !== 'Flower Room' && room !== 'Veg Room') return { status: 400, body: { detail: 'room is required' } }
    if (scenario === 'archive-empty') return { status: 200, body: { items: [], next_cursor: null } }
    const all = entriesFor(room, scenario, fixtureOrigin).map(entry => {
      const jobSummary = entry.job as { job_id?: string; kind?: 'export' | 'load'; state?: FixtureJob['state']; requested_revision?: number } | null
      const jobState = jobSummary?.job_id === undefined
        ? null
        : fixtureJob(fixtureSession, jobSummary.job_id, String(entry.dataset_id), jobSummary.kind ?? 'export', Number(jobSummary.requested_revision ?? entry.revision), jobSummary.state)
      const annualUrl = fileUrl(fixtureOrigin, '2026-r2-fixture-annual.zip')
      const container = entry.container === null
        ? null
        : { ...(entry.container as object), remote_download_url: annualUrl }
      const exportReady = jobState?.state === 'succeeded' && jobSummary?.kind === 'export'
      return {
        ...entry,
        ...(jobSummary === null || jobState === null ? {} : {
          job: { ...jobSummary, state: jobState.state },
        }),
        ...(exportReady ? { state: 'ready', export_status: 'verified' } : {}),
        remote_download_url: container !== null
          ? annualUrl
          : entry.remote_download_url === null && !exportReady
            ? null
            : fileUrl(fixtureOrigin, `${entry.dataset_id}-verified.zip`),
        ...(container === null ? {} : { container }),
      }
    })
    const limit = Math.max(1, Math.min(50, Number(url.searchParams.get('limit') ?? 10)))
    const cursor = url.searchParams.get('cursor')
    const offset = cursor?.startsWith('cursor:') ? Number(cursor.slice('cursor:'.length)) : 0
    const items = all.slice(offset, offset + limit)
    const next = offset + items.length < all.length ? `cursor:${offset + items.length}` : null
    return { status: 200, body: { items, next_cursor: next } }
  }

  const detailMatch = url.pathname.match(/^\/api\/archives\/([0-9a-f-]+)$/i)
  if (detailMatch !== null && method === 'GET') {
    const entry = entryById(detailMatch[1]!, fixtureOrigin, fixtureSession, scenario)
    return entry === null ? { status: 404, body: { detail: 'archive_not_found' } } : { status: 200, body: archiveDetail(entry, scenario) }
  }

  const exportMatch = url.pathname.match(/^\/api\/archives\/([0-9a-f-]+)\/exports$/i)
  if (exportMatch !== null && method === 'POST') {
    const entry = entryById(exportMatch[1]!, fixtureOrigin, fixtureSession, scenario)
    if (entry === null) return { status: 404, body: { detail: 'archive_not_found' } }
    if (entry.kind === 'grow' && entry.lifecycle_state !== 'closed') return { status: 409, body: { detail: { code: 'grow_not_closed' } } }
    const jobId = uuid(12000 + datasetOrdinal(exportMatch[1]!))
    fixtureJob(fixtureSession, jobId, exportMatch[1]!, 'export', Number(entry.revision))
    return { status: 202, body: { job_id: jobId, state: 'pending' } }
  }

  const loadMatch = url.pathname.match(/^\/api\/archives\/([0-9a-f-]+)\/loads$/i)
  if (loadMatch !== null && method === 'POST') {
    const entry = entryById(loadMatch[1]!, fixtureOrigin, fixtureSession, scenario)
    if (entry === null) return { status: 404, body: { detail: 'archive_not_found' } }
    if (entry.published_revision === null) return { status: 409, body: { detail: { code: 'archive_revision_pending' } } }
    if (scenario === 'archive-load-job') {
      const jobId = uuid(14000 + datasetOrdinal(loadMatch[1]!))
      fixtureJob(fixtureSession, jobId, loadMatch[1]!, 'load', Number(entry.published_revision))
      const job = FIXTURE_JOBS.get(`${fixtureSession}:${jobId}`)!
      Object.assign(job, {
        loadId: uuid(16000 + datasetOrdinal(loadMatch[1]!)),
        remoteDownloadUrl: entry.remote_download_url,
        downloadLabel: entry.download_label,
      })
      return { status: 202, body: { job_id: jobId, state: 'pending' } }
    }
    return { status: 200, body: { load_id: uuid(16000 + datasetOrdinal(loadMatch[1]!)), revision: entry.published_revision, state: 'ready', source_kind: 'zip', stale: entry.published_revision !== entry.revision, remote_download_url: entry.remote_download_url, download_label: entry.download_label } }
  }

  const releaseMatch = url.pathname.match(/^\/api\/archives\/loads\/([0-9a-f-]+)$/i)
  if (releaseMatch !== null && method === 'DELETE') return { status: 204, body: null }

  const retryMatch = url.pathname.match(/^\/api\/archives\/jobs\/([0-9a-f-]+)\/retry$/i)
  if (retryMatch !== null && method === 'POST') {
    const existing = FIXTURE_JOBS.get(`${fixtureSession}:${retryMatch[1]}`)
    if (existing === undefined) return { status: 404, body: { detail: 'archive_job_not_found' } }
    existing.polls = 0
    existing.state = 'running'
    return { status: 202, body: { job_id: retryMatch[1], state: 'pending' } }
  }

  const jobMatch = url.pathname.match(/^\/api\/archives\/jobs\/([0-9a-f-]+)$/i)
  if (jobMatch !== null && method === 'GET') {
    const existing = FIXTURE_JOBS.get(`${fixtureSession}:${jobMatch[1]}`)
    return existing === undefined
      ? { status: 404, body: { detail: 'archive_job_not_found' } }
      : { status: 200, body: jobResponse(existing, jobMatch[1]!) }
  }

  const graphMatch = url.pathname.match(/^\/api\/archives\/([0-9a-f-]+)\/graph$/i)
  if (graphMatch !== null && method === 'GET') {
    const entry = entryById(graphMatch[1]!, fixtureOrigin, fixtureSession, scenario)
    if (entry === null) return { status: 404, body: { detail: 'archive_not_found' } }
    if (!url.searchParams.has('load_id')) return { status: 409, body: { detail: { code: 'archive_load_required' } } }
    try {
      return { status: 200, body: graphReply(url, entry, scenario) }
    } catch {
      return { status: 400, body: { detail: 'archive_range_invalid' } }
    }
  }

  if (/^\/api\/archives\/[^/]+\/download$/.test(url.pathname) && method === 'GET') {
    return { status: 409, body: { detail: { code: 'archive_export_required' } } }
  }
  return null
}
