/**
 * Per-panel alignment cache. Historical inputs build one static grid; the live
 * adapter only touches the current tail slot and never re-sorts history.
 */
import type { ControlMonitoringResponse, LiveSensorValue } from '../api'
import type { TimeseriesPanelSpec } from '../config'
import { familyForUnit } from './alignSeries.builder'
import { metricFromName } from './alignSeries.control'
import { alignSeriesBase, applyLiveTail } from './alignSeries'
import type { AlignInput, AlignedData } from './alignSeries.types'

export interface PanelAlignmentInput extends AlignInput {
  readonly panel: TimeseriesPanelSpec
}

export interface PanelAlignmentCounts {
  readonly baseAlignments: number
  readonly liveTailUpdates: number
}

interface BaseKey {
  readonly series: AlignInput['series']
  readonly controlHistory: AlignInput['controlHistory']
  readonly projectionHistory: AlignInput['projectionHistory']
  readonly photoperiod: AlignInput['photoperiod']
  readonly range: AlignInput['range']
  readonly maxPoints: AlignInput['maxPoints']
  readonly panel: TimeseriesPanelSpec
}

interface TailKey {
  readonly live: AlignInput['live']
  readonly second: number
}

interface LiveSnapshot {
  readonly timestamp: number
  readonly values: LiveSensorValue[]
}

export interface PanelAlignment {
  readonly counts: PanelAlignmentCounts
  align(input: PanelAlignmentInput): AlignedData
}

class CachedPanelAlignment implements PanelAlignment {
  private baseKey: BaseKey | null = null
  private base: ReturnType<typeof alignSeriesBase> | null = null
  private tailKey: TailKey | null = null
  private tailResult: AlignedData | null = null
  private liveSnapshots: LiveSnapshot[] = []
  private baseAlignments = 0
  private liveTailUpdates = 0

  get counts(): PanelAlignmentCounts {
    return { baseAlignments: this.baseAlignments, liveTailUpdates: this.liveTailUpdates }
  }

  align(input: PanelAlignmentInput): AlignedData {
    const key = baseKeyFor(input)
    if (!sameBaseKey(this.baseKey, key)) {
      if (!sameLiveSnapshotContext(this.baseKey, key)) this.liveSnapshots = []
      this.base = alignSeriesBase(filterPanelInput(input))
      this.baseKey = key
      this.tailKey = null
      this.baseAlignments++
    }

    const tailKey = { live: input.live, second: Math.floor(input.now.getTime() / 1000) }
    if (this.tailResult !== null && sameTailKey(this.tailKey, tailKey)) return this.tailResult

    const base = this.base
    if (base === null) return alignSeriesBase(filterPanelInput(input)).data
    this.liveSnapshots = recordLiveSnapshot(this.liveSnapshots, input)
    this.tailResult = replayLiveSnapshots(base, this.liveSnapshots)
    this.tailKey = tailKey
    if (input.live.length > 0) this.liveTailUpdates++
    return this.tailResult
  }
}

export function createPanelAlignment(): PanelAlignment {
  return new CachedPanelAlignment()
}

function baseKeyFor(input: PanelAlignmentInput): BaseKey {
  return {
    series: input.series,
    controlHistory: input.controlHistory,
    projectionHistory: input.projectionHistory,
    photoperiod: input.photoperiod,
    range: input.range,
    maxPoints: input.maxPoints,
    panel: input.panel,
  }
}

function sameBaseKey(previous: BaseKey | null, next: BaseKey): boolean {
  return (
    previous !== null &&
    previous.series === next.series &&
    previous.controlHistory === next.controlHistory &&
    previous.projectionHistory === next.projectionHistory &&
    previous.photoperiod === next.photoperiod &&
    previous.range === next.range &&
    previous.maxPoints === next.maxPoints &&
    previous.panel === next.panel
  )
}

function sameTailKey(previous: TailKey | null, next: TailKey): boolean {
  return previous !== null && previous.live === next.live && previous.second === next.second
}

function sameLiveSnapshotContext(previous: BaseKey | null, next: BaseKey): boolean {
  return previous !== null && previous.series === next.series && previous.range === next.range && previous.panel === next.panel
}

function recordLiveSnapshot(snapshots: LiveSnapshot[], input: PanelAlignmentInput): LiveSnapshot[] {
  const now = input.now.getTime()
  const values = input.live.filter((value) => input.series.some(
    (series) => series.sensor === value.sensor && accepts(input.panel, 'sensor', familyForUnit(series.unit_family)),
  ))
  if (input.range.kind === 'fixed') return values.length === 0 ? [] : [{ timestamp: now, values }]
  const start = now - input.range.duration
  const retained = snapshots.filter((snapshot) => snapshot.timestamp >= start && snapshot.timestamp <= now)
  if (values.length === 0) return retained
  const snapshot = { timestamp: now, values }
  const existing = retained.findIndex((candidate) => candidate.timestamp === now)
  if (existing >= 0) return [...retained.slice(0, existing), snapshot, ...retained.slice(existing + 1)]
  return [...retained, snapshot].sort((left, right) => left.timestamp - right.timestamp)
}

function replayLiveSnapshots(
  base: ReturnType<typeof alignSeriesBase>,
  snapshots: LiveSnapshot[],
): AlignedData {
  return snapshots.reduce(
    (data, snapshot) => applyLiveTail({ data, rangeKind: base.rangeKind }, snapshot.values, new Date(snapshot.timestamp)),
    base.data,
  )
}

function filterPanelInput(input: PanelAlignmentInput): Omit<AlignInput, 'live'> {
  const { panel } = input
  return {
    series: input.series.filter(
      (series) => accepts(panel, 'sensor', familyForUnit(series.unit_family)),
    ),
    controlHistory: filterControlResponse(input.controlHistory, panel),
    projectionHistory: filterControlResponse(input.projectionHistory, panel),
    photoperiod: input.photoperiod,
    range: input.range,
    now: input.now,
    maxPoints: input.maxPoints,
    seriesSpecs: panel.series,
    scaleDefaults: panel.defaults,
  }
}

function filterControlResponse(
  response: ControlMonitoringResponse | null,
  panel: TimeseriesPanelSpec,
): ControlMonitoringResponse | null {
  if (response === null) return null
  return {
    ...response,
    climate: response.climate.filter((series) =>
      accepts(panel, 'climate', controlFamilyFromName('climate', series.name)),
    ),
    lights: response.lights.filter((series) =>
      accepts(panel, 'climate', controlFamilyFromName('light', series.name)),
    ),
    devices: response.devices.filter(() => accepts(panel, 'device', 'device')),
    pid: response.pid.filter(() => accepts(panel, 'pid', 'device') || accepts(panel, 'device', 'device')),
  }
}

function controlFamilyFromName(
  kind: 'climate' | 'light',
  name: string,
): TimeseriesPanelSpec['families'][number] {
  if (kind === 'light') return 'light'
  const metric = metricFromName(name)
  if (metric.includes('setpoint')) {
    if (metric.includes('co2')) return 'co2'
    if (metric.includes('vpd')) return 'vpd'
    if (metric.includes('humid')) return 'rh'
    return 'temperature'
  }
  if (metric.includes('light')) return 'light'
  if (metric.includes('vpd')) return 'vpd'
  if (metric.includes('co2')) return 'co2'
  if (metric.includes('rh') || metric.includes('humid')) return 'rh'
  if (metric.includes('pressure')) return 'pressure'
  if (metric.includes('temp')) return 'temperature'
  return 'device'
}

function accepts(
  panel: TimeseriesPanelSpec,
  source: TimeseriesPanelSpec['sources'][number],
  family: TimeseriesPanelSpec['families'][number],
): boolean {
  return panel.sources.includes(source) && panel.families.includes(family)
}
