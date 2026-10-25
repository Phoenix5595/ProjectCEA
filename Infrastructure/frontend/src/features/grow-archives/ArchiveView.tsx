/**
 * Explicitly selected archive graphs, reusing Historical sensor/light charts.
 *
 * This surface accepts only a matching module-local load pin. Graph, detail,
 * download-link, and phase-coverage reads cannot create a load or fetch a ZIP.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { HistoryOverviewChart, HistoryOverviewTables } from '../history-overview'
import type { HistoryOverviewSensorResponse } from '../history-overview/contracts'
import type { FixedRange } from '../monitoring/state'
import type { AlignedBand, AlignedData, AlignedSeries, SeriesKey } from '../monitoring/data'
import type { ChartFamily } from '../monitoring/charts/options/family'
import type { SeriesPresentation } from '../monitoring/data/alignSeries.types'
import { findSpec, familyForUnit, presentationFromSpec } from '../monitoring/data/alignSeries.builder'
import { seriesKey } from '../monitoring/data/alignSeries.types'
import type { MonitoringManifest, SeriesSpec } from '../monitoring/config'
import type { MonitoringResponse, UnitFamily } from '../monitoring/api'
import { splitChartGroups } from '../monitoring/pages/chartGroups'
import { historyBucketBounds } from '../history-overview/buckets'

import { ArchiveApi, isSafeNextcloudFileUrl } from './api'
import type { ArchiveDetail, ArchiveGraph, ArchiveRoom } from './contracts'
import {
  getLoadedSelection,
  releaseLoadedSelection,
  resolveArchiveHistoricalSelection,
  sameSelection,
} from './selection'
import type { ArchiveSelection } from './selection'


type SensorPoint = {
  readonly timestamp: Date
  readonly average: number | null
  readonly minimum: number | null
  readonly maximum: number | null
  readonly sample_count: number
}

type Sample = {
  readonly time: number
  readonly average: number | null
  readonly minimum: number | null
  readonly maximum: number | null
  readonly count: number
}

interface NumericSeries {
  readonly source: 'sensor' | 'light'
  readonly name: string
  readonly label: string
  readonly unit: string
  readonly unitFamily: UnitFamily
  readonly kind: 'sensor' | 'linear'
  readonly node?: string
  readonly aggregated: boolean
  readonly samples: readonly Sample[]
}

export interface ArchiveViewProps {
  readonly selection: ArchiveSelection
  readonly room: ArchiveRoom
  readonly manifest: MonitoringManifest
  /** Used on the Monitoring hand-off route; Historical owns its graph range locally. */
  readonly initialRange?: FixedRange
  readonly onBackToLog: (range: FixedRange) => void
  /** Called only after the server confirms release of this load pin. */
  readonly onExit: () => void
  /** A short selection from the seven-day Historical archive view. */
  readonly onHandoffToMonitoring?: (range: FixedRange) => void
}

function errorCode(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = error.response
    const data = typeof response === 'object' && response !== null && 'data' in response ? response.data : undefined
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : undefined
    if (typeof detail === 'string') return detail
    if (typeof detail === 'object' && detail !== null && 'code' in detail) {
      const code = detail.code
      if (typeof code === 'string') return code
    }
  }
  if (error instanceof Error && error.message.length > 0) return error.message
  return 'archive_service_unavailable'
}

function formatInstant(value: Date | null): string {
  if (value === null) return 'Unknown'
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Toronto',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(value)
}

function manifestSpecs(manifest: MonitoringManifest): SeriesSpec[] {
  return manifest.panels.flatMap(panel => (panel.kind === 'timeseries' ? panel.series : []))
}

function overviewSamples(overview: HistoryOverviewSensorResponse): NumericSeries[] {
  return overview.series.map(series => ({
    source: 'sensor' as const,
    name: series.sensor,
    label: series.sensor,
    unit: series.unit,
    unitFamily: series.unit_family,
    kind: 'sensor' as const,
    node: series.node,
    aggregated: true,
    samples: series.points.map(point => ({
      time: (point.start.getTime() + point.end.getTime()) / 2,
      average: point.sample_count > 0 ? point.average : null,
      minimum: point.sample_count > 0 ? point.minimum : null,
      maximum: point.sample_count > 0 ? point.maximum : null,
      count: point.sample_count,
    })),
  }))
}

function detailedSamples(response: MonitoringResponse): NumericSeries[] {
  return response.series.map(series => ({
    source: 'sensor' as const,
    name: series.sensor,
    label: series.sensor,
    unit: series.unit,
    unitFamily: series.unit_family,
    kind: 'sensor' as const,
    node: series.node,
    aggregated: response.metadata.tier !== 'raw',
    samples: series.points.map((point: SensorPoint) => ({
      time: point.timestamp.getTime(),
      average: point.sample_count > 0 ? point.average : null,
      minimum: point.sample_count > 0 ? point.minimum : null,
      maximum: point.sample_count > 0 ? point.maximum : null,
      count: point.sample_count,
    })),
  }))
}

function allNumericSeries(graph: ArchiveGraph): NumericSeries[] {
  const sensorSeries = graph.tier === 'overview'
    ? overviewSamples(graph.sensors)
    : detailedSamples(graph.sensors)
  const lightSeries: NumericSeries[] = graph.lights.map(series => ({
    source: 'light' as const,
    name: series.device_name,
    label: series.device_name,
    unit: '%',
    unitFamily: 'percent' as const,
    kind: 'linear' as const,
    aggregated: true,
    samples: series.points.map(point => ({
      time: (point.start.getTime() + point.end.getTime()) / 2,
      average: point.sample_count > 0 ? point.average : null,
      minimum: point.sample_count > 0 ? point.minimum : null,
      maximum: point.sample_count > 0 ? point.maximum : null,
      count: point.sample_count,
    })),
  }))
  return [...sensorSeries, ...lightSeries]
}

function makeRoleSeries(
  identity: NumericSeries,
  role: 'mean' | 'min' | 'max',
  values: Array<number | null>,
  key: SeriesKey,
  label: string,
  family: ChartFamily,
  presentation: SeriesPresentation | undefined
): AlignedSeries {
  return {
    key,
    label,
    kind: identity.kind,
    source: identity.source,
    metric: identity.name,
    ...(identity.node === undefined ? {} : { node: identity.node }),
    family,
    role,
    y: values,
    origin: 'recorded',
    quality: 'exact',
    isAggregated: identity.aggregated,
    preserveGaps: true,
    unit: identity.unit,
    unitFamily: identity.unitFamily,
    presentation,
  }
}

function buildArchiveAlignedData(
  graph: ArchiveGraph,
  manifest: MonitoringManifest
): AlignedData {
  const numeric = allNumericSeries(graph)
  const x = [...new Set(numeric.flatMap(series => series.samples.map(sample => sample.time)))].sort((a, b) => a - b)
  const indexByTime = new Map(x.map((time, index) => [time, index]))
  const specs = manifestSpecs(manifest)
  const usedMetrics: Record<'sensor' | 'light', Set<string>> = {
    sensor: new Set(),
    light: new Set(),
  }
  const series: AlignedSeries[] = []
  const bands: AlignedBand[] = []

  for (const item of numeric) {
    if (!item.samples.some(sample =>
      sample.count > 0 &&
      sample.average !== null && Number.isFinite(sample.average) &&
      sample.minimum !== null && Number.isFinite(sample.minimum) &&
      sample.maximum !== null && Number.isFinite(sample.maximum)
    )) continue

    const spec = findSpec(specs, item.name)
    let metric = item.name
    let suffix = 2
    while (usedMetrics[item.source].has(metric)) {
      metric = `${item.name}#${suffix}`
      suffix += 1
    }
    usedMetrics[item.source].add(metric)
    const identity = { ...item, name: metric }
    const keys = {
      mean: seriesKey(item.source, metric, 'mean'),
      min: seriesKey(item.source, metric, 'min'),
      max: seriesKey(item.source, metric, 'max'),
      band: seriesKey(item.source, metric, 'band'),
    }
    const average = Array<number | null>(x.length).fill(null)
    const minimum = Array<number | null>(x.length).fill(null)
    const maximum = Array<number | null>(x.length).fill(null)
    for (const sample of item.samples) {
      const index = indexByTime.get(sample.time)
      if (index === undefined || sample.count === 0) continue
      if (sample.average !== null && Number.isFinite(sample.average)) average[index] = sample.average
      if (sample.minimum !== null && Number.isFinite(sample.minimum)) minimum[index] = sample.minimum
      if (sample.maximum !== null && Number.isFinite(sample.maximum)) maximum[index] = sample.maximum
    }
    const displayName = spec?.displayName ?? item.label
    const family = item.source === 'light' ? 'light' : familyForUnit(item.unitFamily)
    const presentation = presentationFromSpec(spec)
    series.push(
      makeRoleSeries(identity, 'mean', average, keys.mean, displayName, family, presentation),
      makeRoleSeries(identity, 'min', minimum, keys.min, `${displayName} min`, family, presentation),
      makeRoleSeries(identity, 'max', maximum, keys.max, `${displayName} max`, family, presentation)
    )
    bands.push({ key: keys.band, minKey: keys.min, maxKey: keys.max })
  }

  const overview = 'bucket' in graph.sensors.metadata
    ? {
        buckets: historyBucketBounds(
          graph.sensors.metadata.range.start,
          graph.sensors.metadata.range.end,
          graph.sensors.metadata.bucket
        ).map(bucket => ({ start: bucket.start.getTime(), end: bucket.end.getTime() })),
      }
    : undefined
  return {
    x,
    series,
    bands,
    photoperiod: [],
    nowIndex: -1,
    nowMs: x[x.length - 1] ?? Date.now(),
    sensorIntervalSeconds: null,
    aggregated: graph.tier === 'overview',
    ...(overview === undefined ? {} : { overview }),
  }
}

function sourceLabel(room: ArchiveRoom): string {
  return room === 'Flower Room' ? 'Flower' : 'Veg'
}

function sameGraphRange(graph: ArchiveGraph, range: FixedRange): boolean {
  const response = graph.sensors.metadata.range
  return response.start.getTime() === range.start.getTime() && response.end.getTime() === range.end.getTime()
}

interface ArchiveFactsProps {
  readonly detail: ArchiveDetail | null
  readonly graph: ArchiveGraph | null
  readonly gaps: ArchiveGraph['archive']['gaps']
}

function ArchiveFacts({ detail, graph, gaps }: ArchiveFactsProps) {
  const [groupIndex, setGroupIndex] = useState(0)
  const [recordIndex, setRecordIndex] = useState(0)
  const [textPage, setTextPage] = useState(0)
  const groups = useMemo(() => [
    { label: 'Calendar phase windows', records: detail?.phases ?? [] },
    { label: 'Known half-open data gaps', records: gaps },
    { label: 'Recorded photoperiod phases', records: graph?.photoperiod ?? [] },
    { label: 'Recorded photoperiod coverage', records: graph?.photoperiod_coverage ?? [] },
    { label: 'Source availability', records: detail?.availability ?? [] },
    {
      label: 'Source schema',
      records: Object.entries(detail?.schema.source_schema ?? {})
        .map(([table, schema]) => ({ table, schema })),
    },
    {
      label: 'Graph schema',
      records: Object.entries(detail?.schema.graph_schema ?? {})
        .map(([name, schema]) => ({ name, schema })),
    },
    { label: 'Frozen sensor/device identities', records: detail?.identity_snapshots ?? [] },
  ], [detail, gaps, graph])
  const group = groups[groupIndex]
  const selectedRecord = Math.min(recordIndex, Math.max(0, group.records.length - 1))
  const lines = useMemo(() => {
    const record = group.records[selectedRecord]
    if (record === undefined) return []
    // Bound rendered text, not the metadata itself. Every original field and
    // long value remains reachable without adding a nested scrolling surface.
    return JSON.stringify(record, null, 2).split('\n')
      .flatMap(line => line.match(/.{1,48}/gu) ?? [''])
  }, [group, selectedRecord])
  const pageCount = Math.max(1, Math.ceil(lines.length / 8))
  const selectedPage = Math.min(textPage, pageCount - 1)

  return (
    <section className="mon-card" aria-label="Archive phases and recorded coverage">
      <h2 className="mon-card__title">Recorded phase and coverage facts</h2>
      {detail !== null && (
        <p className="text-xs text-text-muted">
          Catalog metadata: published {detail.published_revision === null ? 'none' : `r${detail.published_revision}`}
          {' · '}current r{detail.revision}
        </p>
      )}
      {graph !== null && (
        <p className="text-xs text-text-muted">Recorded graph facts: loaded r{graph.archive.revision}</p>
      )}
      <label className="block text-sm">
        Fact group
        <select
          className="mt-1 w-full rounded border border-border-default bg-surface-secondary p-1"
          value={groupIndex}
          onChange={event => {
            setGroupIndex(Number(event.target.value))
            setRecordIndex(0)
            setTextPage(0)
          }}
        >
          {groups.map((item, index) => (
            <option key={item.label} value={index}>{item.label} ({item.records.length})</option>
          ))}
        </select>
      </label>
      {group.records.length === 0 ? (
        <p role="status" className="mt-2 text-sm text-text-muted">No records in this group.</p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-1 text-xs">
            <button type="button" className="mon-toolbar__preset" disabled={selectedRecord === 0}
              onClick={() => { setRecordIndex(selectedRecord - 1); setTextPage(0) }}>
              Previous record
            </button>
            <span role="status">Record {selectedRecord + 1} / {group.records.length}</span>
            <button type="button" className="mon-toolbar__preset"
              disabled={selectedRecord + 1 >= group.records.length}
              onClick={() => { setRecordIndex(selectedRecord + 1); setTextPage(0) }}>
              Next record
            </button>
          </div>
          <pre className="mt-2 whitespace-pre-wrap break-all text-xs" aria-label="Selected archive fact">
            {lines.slice(selectedPage * 8, (selectedPage + 1) * 8).join('\n')}
          </pre>
          {pageCount > 1 && (
            <div className="mt-2 flex items-center justify-between gap-1 text-xs">
              <button type="button" className="mon-toolbar__preset" disabled={selectedPage === 0}
                onClick={() => setTextPage(selectedPage - 1)}>Earlier lines</button>
              <span>Text page {selectedPage + 1} / {pageCount}</span>
              <button type="button" className="mon-toolbar__preset" disabled={selectedPage + 1 >= pageCount}
                onClick={() => setTextPage(selectedPage + 1)}>More lines</button>
            </div>
          )}
          <p className="mt-2 text-xs text-text-muted">ISO instants are UTC; calendar dates are local.</p>
        </>
      )}
    </section>
  )
}

export function ArchiveView({
  selection,
  room,
  manifest,
  initialRange,
  onBackToLog,
  onExit,
  onHandoffToMonitoring,
}: ArchiveViewProps) {
  const api = useMemo(() => new ArchiveApi(), [])
  const selectionStart = new Date(selection.start)
  const selectionEnd = new Date(selection.end)
  const selectionStartMs = selectionStart.getTime()
  const selectionEndMs = selectionEnd.getTime()
  const [range, setRange] = useState<FixedRange>(() => {
    const candidate = initialRange ?? { kind: 'fixed' as const, start: selectionStart, end: selectionEnd }
    const start = new Date(Math.max(candidate.start.getTime(), selectionStart.getTime()))
    const end = new Date(Math.min(candidate.end.getTime(), selectionEnd.getTime()))
    return end.getTime() > start.getTime()
      ? { kind: 'fixed', start, end }
      : { kind: 'fixed', start: selectionStart, end: selectionEnd }
  })
  const [detail, setDetail] = useState<ArchiveDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [graph, setGraph] = useState<ArchiveGraph | null>(null)
  const [graphLoading, setGraphLoading] = useState(true)
  const [graphError, setGraphError] = useState<string | null>(null)
  const [retryRevision, setRetryRevision] = useState(0)
  const [statisticPage, setStatisticPage] = useState(0)
  const [releasing, setReleasing] = useState(false)
  const [releaseError, setReleaseError] = useState<string | null>(null)

  // Apply the handed-off range once at mount; later navigation inside the
  // archive owns its own range and must never be overwritten by a stale
  // handoff prop.
  const initialRangeApplied = useRef(initialRange === undefined)
  useEffect(() => {
    if (initialRangeApplied.current) return
    if (initialRange === undefined) return
    initialRangeApplied.current = true
    const clippedStart = Math.max(initialRange.start.getTime(), selectionStartMs)
    const clippedEnd = Math.min(initialRange.end.getTime(), selectionEndMs)
    if (clippedEnd > clippedStart) {
      setRange({ kind: 'fixed', start: new Date(clippedStart), end: new Date(clippedEnd) })
    }
  }, [initialRange, selectionEndMs, selectionStartMs])

  useEffect(() => {
    const controller = new AbortController()
    setDetail(null)
    setDetailError(null)
    void api.detail(selection.dataset_id, controller.signal).then(
      result => {
        if (controller.signal.aborted) return
        if (result.room !== room) {
          setDetailError('archive_room_mismatch')
          return
        }
        setDetail(result)
      },
      error => {
        if (!controller.signal.aborted) setDetailError(errorCode(error))
      }
    )
    return () => controller.abort()
  }, [api, room, selection.dataset_id])

  useEffect(() => {
    const controller = new AbortController()
    setGraph(null)
    setGraphLoading(true)
    setGraphError(null)
    const registered = getLoadedSelection(selection.load_id)
    if (registered === undefined || !sameSelection(registered, selection)) {
      setGraphLoading(false)
      setGraphError('archive_load_required')
      return () => controller.abort()
    }
    void api.graph(
      selection.dataset_id,
      selection.load_id,
      range,
      controller.signal
    ).then(
      result => {
        if (controller.signal.aborted) return
        if (result.archive.revision !== selection.revision || !sameGraphRange(result, range)) {
          setGraphError('archive_integrity_failed')
          setGraphLoading(false)
          return
        }
        setGraph(result)
        setGraphLoading(false)
      },
      error => {
        if (controller.signal.aborted) return
        setGraphError(errorCode(error))
        setGraphLoading(false)
      }
    )
    return () => controller.abort()
  }, [api, range, retryRevision, selection.dataset_id, selection.load_id, selection.revision])

  const alignedState = useMemo(() => {
    if (graph === null) return { data: null as AlignedData | null, error: null as string | null }
    try {
      return { data: buildArchiveAlignedData(graph, manifest), error: null }
    } catch {
      return { data: null, error: 'archive_integrity_failed' }
    }
  }, [graph, manifest, range])
  const chartGroups = useMemo(
    () => alignedState.data === null ? null : splitChartGroups(manifest, alignedState.data),
    [alignedState.data, manifest]
  )
  const chartTitles = manifest.panels
    .flatMap(panel => panel.kind === 'timeseries' ? [panel.title] : [])
  const hasChartSeries = chartGroups !== null &&
    (chartGroups.climate.series.length > 0 || chartGroups.device.series.length > 0)
  const secondaryHasLight = chartGroups?.device.series.some(series => series.source === 'light') ?? false
  const secondaryHasSensors = chartGroups?.device.series.some(series => series.source === 'sensor') ?? false
  const secondaryTitle = secondaryHasLight
    ? secondaryHasSensors ? 'Recorded light and additional sensors' : 'Recorded light intensity'
    : 'Recorded additional sensor measurements'
  const staleRevision = detail !== null && detail.revision !== selection.revision
  const coverage = graph?.archive.coverage ?? detail?.coverage ?? 'unknown'
  const gaps = graph?.archive.gaps ?? detail?.gap_intervals ?? []
  const statisticCount = graph?.sensors.statistics.length ?? 0
  const statisticPages = Math.max(1, Math.ceil(statisticCount / 6))
  const visibleStatisticPage = Math.min(statisticPage, statisticPages - 1)
  const nextcloudUrl = isSafeNextcloudFileUrl(selection.remote_download_url)
    ? selection.remote_download_url
    : null

  const changeSelection = useCallback((start: Date, end: Date): void => {
    const resolved = resolveArchiveHistoricalSelection(range, start, end, selection)
    if (resolved === null) {
      setGraphError('archive_range_outside_dataset')
      return
    }
    const next: FixedRange = { kind: 'fixed', start: resolved.start, end: resolved.end }
    if (resolved.surface === 'monitoring' && onHandoffToMonitoring !== undefined) {
      onHandoffToMonitoring(next)
      return
    }
    setRange(next)
  }, [onHandoffToMonitoring, range, selection])

  const exit = async (): Promise<void> => {
    setReleasing(true)
    setReleaseError(null)
    try {
      await api.releaseLoad(selection.load_id)
      releaseLoadedSelection(selection.load_id)
      onExit()
    } catch (error) {
      setReleaseError(errorCode(error))
    } finally {
      setReleasing(false)
    }
  }

  const currentSpan = `${formatInstant(range.start)} – ${formatInstant(range.end)} (end exclusive)`
  const title = detail?.name ?? `${sourceLabel(room)} archive`
  const pendingCoverage = coverage === 'gapped' ? 'Known gaps are retained and shown below.' : null

  return (
    <div className="archive-view">
      <section className="mon-card" aria-label="Selected archived history">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="mon-card__title">Archived {sourceLabel(room)} history — {title}</h2>
            <p className="text-sm text-text-muted">
              Loaded revision r{selection.revision}
              {staleRevision ? ` · current catalog revision r${detail?.revision} is not published in this view` : ''}
            </p>
            <p className="text-sm text-text-muted">Viewing {currentSpan}</p>
            <p className="text-sm">
              Coverage: <strong>{coverage}</strong>
              {pendingCoverage === null ? '' : ` — ${pendingCoverage}`}
              {gaps.length > 0 ? ` (${gaps.length} known gap${gaps.length === 1 ? '' : 's'})` : ''}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="mon-toolbar__preset" onClick={() => onBackToLog(range)}>
              Back to archive log
            </button>
            <button type="button" className="mon-toolbar__preset" disabled={releasing} onClick={() => void exit()}>
              {releasing ? 'Releasing…' : 'Exit to Monitoring'}
            </button>
            {nextcloudUrl !== null && (
              <a
                className="mon-toolbar__preset underline decoration-accent underline-offset-2"
                href={nextcloudUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                {selection.download_label ?? 'Open selected ZIP in Nextcloud'}
              </a>
            )}
          </div>
        </div>
        {releaseError !== null && (
          <div className="mon-banner mon-banner--error" role="alert">
            Load release failed: {releaseError}. The selected archive remains open.
          </div>
        )}
        {detailError !== null && (
          <div className="mon-banner mon-banner--error" role="alert">
            Archive metadata unavailable: {detailError}
          </div>
        )}
        {detail === null && detailError === null && (
          <div role="status" className="mon-banner">Loading archive metadata…</div>
        )}
        {detail !== null && (
          <div className="mt-2 text-sm text-text-muted">
            Local tier: {detail.local_tier.replaceAll('_', ' ')} · Export: {detail.export_status.replaceAll('_', ' ')}
            {detail.anniversary_at === null ? '' : ` · Original anniversary ${formatInstant(detail.anniversary_at)}`}
          </div>
        )}
      </section>

      {graphLoading && <div role="status" className="mon-banner">Loading selected archive graph…</div>}
      {graphError !== null && !graphLoading && (
        <div className="mon-banner mon-banner--error" role="alert">
          Archive graph unavailable: {graphError}
          <button type="button" onClick={() => setRetryRevision(value => value + 1)}>Retry graph</button>
        </div>
      )}
      {alignedState.error !== null && (
        <div className="mon-banner mon-banner--error" role="alert">Archive graph failed validation: {alignedState.error}</div>
      )}
      {graph !== null && alignedState.data !== null && alignedState.data.series.length === 0 && (
        <div role="status" className="mon-banner">No recorded sensor or light observations in this selected interval.</div>
      )}
      {graph !== null && alignedState.data !== null && alignedState.data.series.length > 0 && !hasChartSeries && (
        <div role="status" className="mon-banner">Recorded series are not part of the selected room's sensor or light panels.</div>
      )}
      <div className="archive-view__layout">
      {chartGroups !== null && alignedState.data !== null && hasChartSeries && (
        <div className="mon-main archive-view__charts">
          {chartGroups.climate.series.length > 0 && (
            <section className="mon-card" aria-label={chartTitles[0] ?? 'Archived climate observations'}>
              <h2 className="mon-card__title">{chartTitles[0] ?? 'Archived climate observations'}</h2>
              <div className="archive-view__plot">
                <HistoryOverviewChart
                  data={chartGroups.climate}
                  range={range}
                  onDetail={changeSelection}
                  historicalDrillDown={true}
                  label={chartTitles[0] ?? 'Archived climate observations'}
                />
              </div>
            </section>
          )}
          {chartGroups.device.series.length > 0 && (
            <section className="mon-card" aria-label={secondaryTitle}>
              <h2 className="mon-card__title">{secondaryTitle}</h2>
              <div className="archive-view__plot">
                <HistoryOverviewChart
                  data={chartGroups.device}
                  range={range}
                  onDetail={changeSelection}
                  historicalDrillDown={true}
                  label={secondaryTitle}
                />
              </div>
            </section>
          )}
        </div>
      )}
      <aside className="mon-side archive-view__side">
      {graph !== null && (
        <section className="mon-card" aria-label="Archived sensor statistics">
          <h2 className="mon-card__title">Recorded sensor statistics</h2>
          <HistoryOverviewTables
            statistics={graph.sensors.statistics.slice(visibleStatisticPage * 6, (visibleStatisticPage + 1) * 6)}
            manifest={manifest}
          />
          {statisticPages > 1 && (
            <div className="mt-2 flex items-center justify-between gap-1 text-xs">
              <button type="button" className="mon-toolbar__preset" disabled={visibleStatisticPage === 0}
                onClick={() => setStatisticPage(visibleStatisticPage - 1)}>Previous sensors</button>
              <span>Page {visibleStatisticPage + 1} / {statisticPages}</span>
              <button type="button" className="mon-toolbar__preset" disabled={visibleStatisticPage + 1 >= statisticPages}
                onClick={() => setStatisticPage(visibleStatisticPage + 1)}>Next sensors</button>
            </div>
          )}
          {(graph.sensors.statistics.length === 0) && (
            <p className="text-sm text-text-muted">No recorded sensor statistics in this interval.</p>
          )}
        </section>
      )}

      {(detail !== null || graph !== null) && <ArchiveFacts detail={detail} graph={graph} gaps={gaps} />}
      </aside>
      </div>
    </div>
  )
}
