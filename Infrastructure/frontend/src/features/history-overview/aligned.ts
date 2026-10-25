/**
 * Overview response → shared aligned chart-data builder.
 *
 * One shared x grid of exact bucket midpoints (one per lattice bucket), with
 * sampled average/min/max curves for sensor/light identities and — gated by
 * `includeAutomation` — requested-PID identities plus honest per-lane
 * ON/OFF/unknown duration fractions for recorded relays. Values are always
 * null-latticed (gaps never connect), provenance is
 * `derived`+`exact`, and every series carries the canonical manifest labels,
 * colors, and units where a spec exists.
 */
import type {
  AlignedBand,
  AlignedData,
  AlignedSeries,
  SeriesKey,
  SeriesKind,
  SeriesPresentation,
  SeriesSource,
} from '../monitoring/data'
import { alignPhotoperiod } from '../monitoring/data'
import { seriesKey } from '../monitoring/data/alignSeries.types'
import {
  controlFamily,
  findSpec,
  familyForUnit,
  presentationFromSpec,
} from '../monitoring/data/alignSeries.builder'
import { metricFromName } from '../monitoring/data/alignSeries.control'
import type { ChartFamily } from '../monitoring/charts/options/family'
import type { MonitoringManifest, SeriesSpec } from '../monitoring/config'
import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  UnitFamily,
} from '../monitoring/api'

import type {
  HistoryOverviewControlResponse,
  HistoryOverviewControlSeries,
  HistoryOverviewPoint,
  HistoryOverviewRelaySeries,
  HistoryOverviewSensorResponse,
  HistoryOverviewSensorSeries,
} from './contracts'
import { historyBucketBounds } from './buckets'
import type { HistoryBucketBoundsEntry } from './buckets'

export interface HistoryOverviewAlignedOptions {
  readonly sensor?: HistoryOverviewSensorResponse | null
  readonly control?: HistoryOverviewControlResponse | null
  /**
   * Recorded climate history (scope=climate) for the same fixed range. Room
   * charts draw its setpoint lines; every overview keeps its photoperiod for
   * the sun/moon overlay.
   */
  readonly climate?: ControlMonitoringResponse | null
  /** Room manifest supplying canonical display names/colors/soft bounds. */
  readonly manifest?: MonitoringManifest
  /**
   * Automation opt-in for the requested-PID and relay-time groups. Room
   * Monitoring/Historical charts (`false`) must never emit them — even if a
   * full-scope test payload is passed; the automation relay/PID page passes
   * `true`. Sampled light and sensor groups are always retained.
   */
  readonly includeAutomation: boolean
}

type RelayPoint = HistoryOverviewRelaySeries['points'][number]

type RelayRatioKey = 'on' | 'off' | 'unknown'

/** Relay lane hue parity with the detailed relay-timeline interval canvas. */
const RELAY_RATIO_COLORS: Record<RelayRatioKey, string> = {
  on: 'rgb(34, 197, 94)',
  off: 'rgb(100, 116, 139)',
  unknown: 'rgb(148, 163, 184)',
}

const RELAY_RATIO_LABELS: Record<RelayRatioKey, string> = {
  on: 'ON time',
  off: 'OFF time',
  unknown: 'Unknown time',
}

interface SampleIdentity {
  readonly source: SeriesSource
  readonly family: ChartFamily
  readonly metric: string
  readonly label: string
  readonly unit: string
  readonly unitFamily: UnitFamily
  readonly kind: SeriesKind
  readonly presentation?: SeriesPresentation
  readonly node?: string
}

function emptyOverviewData(): AlignedData {
  return {
    x: [],
    series: [],
    bands: [],
    photoperiod: [],
    nowIndex: -1,
    nowMs: Date.now(),
    sensorIntervalSeconds: null,
    aggregated: false,
    overview: { buckets: [] },
  }
}

function manifestSeriesSpecs(manifest: MonitoringManifest | undefined): SeriesSpec[] {
  if (manifest === undefined) return []
  return manifest.panels.flatMap(panel => (panel.kind === 'timeseries' ? panel.series : []))
}

/** Allocate a metric that stays unique inside one overview response. */
function uniqueMetric(used: Set<string>, base: string): string {
  let metric = base
  let suffix = 2
  while (used.has(metric)) {
    metric = `${base}#${suffix}`
    suffix += 1
  }
  used.add(metric)
  return metric
}

function latticeMidpoints(buckets: readonly HistoryBucketBoundsEntry[]): number[] {
  return buckets.map(bucket => (bucket.start.getTime() + bucket.end.getTime()) / 2)
}

function hasSamples(points: readonly { sample_count: number }[]): boolean {
  return points.some(point => point.sample_count > 0)
}

/** Lattice-aligned `(number | null)` values read from validated points. */
function sampleValues(
  buckets: readonly HistoryBucketBoundsEntry[],
  points: readonly HistoryOverviewPoint[],
  read: (point: HistoryOverviewPoint) => number | null
): (number | null)[] {
  const valuesByStart = new Map<number, number | null>()
  for (const point of points) valuesByStart.set(point.start.getTime(), read(point))
  return buckets.map(bucket => {
    const value = valuesByStart.get(bucket.start.getTime())
    return value === undefined ? null : value
  })
}

interface SampleBucketDetail {
  readonly minimum: (number | null)[]
  readonly maximum: (number | null)[]
  readonly sampleCount: (number | null)[]
}

function sampledSeries(
  identity: SampleIdentity,
  role: 'mean' | 'min' | 'max',
  values: (number | null)[],
  bucketDetail?: SampleBucketDetail
): AlignedSeries {
  return {
    key: seriesKey(identity.source, identity.metric, role) as SeriesKey,
    label: role === 'mean' ? identity.label : `${identity.label} ${role}`,
    kind: identity.kind,
    source: identity.source,
    metric: identity.metric,
    family: identity.family,
    role,
    y: values,
    origin: 'derived',
    quality: 'exact',
    isAggregated: true,
    preserveGaps: true,
    unit: identity.unit,
    unitFamily: identity.unitFamily,
    presentation: identity.presentation,
    ...(bucketDetail === undefined ? {} : bucketDetail),
    ...(identity.node !== undefined ? { node: identity.node } : {}),
  }
}

/** Emit the mean/min/max trio plus its envelope band for a sampled identity. */
function addSampledGroup(
  identity: SampleIdentity,
  points: readonly HistoryOverviewPoint[],
  buckets: readonly HistoryBucketBoundsEntry[],
  bands: AlignedBand[],
  series: AlignedSeries[]
): void {
  if (!hasSamples(points)) {
    // An identity without any samples stays visible as an unavailable curve.
    series.push({
      key: seriesKey(identity.source, identity.metric, 'mean') as SeriesKey,
      label: identity.label,
      kind: identity.kind,
      source: identity.source,
      metric: identity.metric,
      family: identity.family,
      role: 'mean',
      y: buckets.map(() => null),
      origin: 'derived',
      quality: 'unavailable',
      isAggregated: true,
      preserveGaps: true,
      unit: identity.unit,
      unitFamily: identity.unitFamily,
      presentation: identity.presentation,
      ...(identity.node !== undefined ? { node: identity.node } : {}),
    })
    return
  }
  series.push(
    sampledSeries(
      identity,
      'mean',
      sampleValues(buckets, points, point => point.average),
      identity.source === 'sensor'
        ? {
            // R4: tooltip bucket span/count/min–max; min/max stay null for a
            // single-sample bucket so no band is implied.
            minimum: sampleValues(buckets, points, point =>
              point.sample_count > 1 ? point.minimum : null
            ),
            maximum: sampleValues(buckets, points, point =>
              point.sample_count > 1 ? point.maximum : null
            ),
            sampleCount: sampleValues(buckets, points, point => point.sample_count),
          }
        : undefined
    )
  )
  series.push(
    sampledSeries(
      identity,
      'min',
      sampleValues(buckets, points, point => point.minimum)
    )
  )
  series.push(
    sampledSeries(
      identity,
      'max',
      sampleValues(buckets, points, point => point.maximum)
    )
  )
  bands.push({
    key: seriesKey(identity.source, identity.metric, 'band') as SeriesKey,
    minKey: seriesKey(identity.source, identity.metric, 'min') as SeriesKey,
    maxKey: seriesKey(identity.source, identity.metric, 'max') as SeriesKey,
  })
}

function sensorIdentity(
  series: HistoryOverviewSensorSeries,
  specs: SeriesSpec[],
  usedMetrics: Set<string>
): SampleIdentity {
  const rawName = series.sensor
  const spec = findSpec(specs, rawName)
  return {
    source: 'sensor',
    family: familyForUnit(series.unit_family),
    metric: uniqueMetric(usedMetrics, rawName),
    label: spec?.displayName ?? rawName,
    unit: series.unit,
    unitFamily: series.unit_family,
    kind: 'sensor',
    presentation: presentationFromSpec(spec),
    node: series.node,
  }
}

function lightIdentity(
  series: HistoryOverviewControlSeries,
  specs: SeriesSpec[],
  usedMetrics: Set<string>
): SampleIdentity {
  const rawName = series.device_name
  const spec = findSpec(specs, rawName)
  return {
    source: 'light',
    family: 'light',
    metric: uniqueMetric(usedMetrics, rawName),
    label: spec?.displayName ?? rawName,
    unit: '%',
    unitFamily: 'percent',
    kind: 'linear',
    presentation: presentationFromSpec(spec),
  }
}

function pidIdentity(
  series: HistoryOverviewControlSeries,
  specs: SeriesSpec[],
  usedMetrics: Set<string>
): SampleIdentity {
  const rawName = series.device_name
  const spec = findSpec(specs, `${rawName} - PID Output`)
  return {
    source: 'pid',
    family: 'device',
    metric: uniqueMetric(usedMetrics, rawName),
    label: spec?.displayName ?? `${rawName} - PID Output`,
    unit: '%',
    unitFamily: 'percent',
    kind: 'point',
    presentation: presentationFromSpec(spec),
  }
}

/** Emit per-lane fractions of bucket duration; never duty-cycle semantics. */
function addRelayLane(
  relay: HistoryOverviewRelaySeries,
  buckets: readonly HistoryBucketBoundsEntry[],
  usedMetrics: Set<string>,
  series: AlignedSeries[]
): void {
  if (relay.points.length === 0) return
  const laneMetric = uniqueMetric(usedMetrics, `${relay.device_name} R${relay.channel + 1}`)
  const pointByStart = new Map<number, RelayPoint>()
  for (const point of relay.points) pointByStart.set(point.start.getTime(), point)
  const ratios: Record<RelayRatioKey, (number | null)[]> = { on: [], off: [], unknown: [] }
  for (const bucket of buckets) {
    const point = pointByStart.get(bucket.start.getTime())
    if (point === undefined) {
      ratios.on.push(null)
      ratios.off.push(null)
      ratios.unknown.push(null)
      continue
    }
    const bucketSeconds = (bucket.end.getTime() - bucket.start.getTime()) / 1000
    const seconds: Record<RelayRatioKey, number> = {
      on: point.on_seconds,
      off: point.off_seconds,
      unknown: point.unknown_seconds,
    }
    for (const ratioKey of ['on', 'off', 'unknown'] as const) {
      ratios[ratioKey].push(bucketSeconds === 0 ? null : (seconds[ratioKey] / bucketSeconds) * 100)
    }
  }
  for (const ratioKey of ['on', 'off', 'unknown'] as const) {
    series.push({
      key: seriesKey('device', `${laneMetric}·${ratioKey}`, 'mean') as SeriesKey,
      label: `${laneMetric} ${RELAY_RATIO_LABELS[ratioKey]}`,
      kind: 'linear',
      source: 'device',
      metric: laneMetric,
      family: 'device',
      role: 'mean',
      y: ratios[ratioKey],
      origin: 'derived',
      quality: 'exact',
      isAggregated: true,
      preserveGaps: true,
      unit: '%',
      unitFamily: 'percent',
      presentation: {
        color: RELAY_RATIO_COLORS[ratioKey],
        softMin: 0,
        softMax: 100,
      },
    })
  }
}

/** Step-hold recorded climate targets onto the overview bucket grid (R2). */
function climateOverviewSeries(
  raw: ClimateTimelineSeries,
  buckets: readonly HistoryBucketBoundsEntry[],
  specs: SeriesSpec[],
  usedMetrics: Set<string>
): AlignedSeries | null {
  const baseMetric = raw.metric ?? raw.points[0]?.metric ?? metricFromName(raw.name)
  const metric = uniqueMetric(usedMetrics, baseMetric)
  const entries = [...raw.points, ...raw.steps]
    .map(point => ({ time: point.time.getTime(), value: point.value }))
    .sort((left, right) => left.time - right.time)
  let cursor = 0
  let held: number | null = null
  const y = buckets.map(bucket => {
    const end = bucket.end.getTime()
    while (cursor < entries.length && (entries[cursor]?.time ?? Infinity) < end) {
      held = entries[cursor]?.value ?? null
      cursor += 1
    }
    return held
  })
  if (!y.some(value => value !== null)) return null
  const spec = findSpec(specs, raw.name)
  return {
    key: seriesKey('climate', metric, 'step') as SeriesKey,
    label: spec?.displayName ?? raw.name,
    kind: 'step',
    source: 'climate',
    metric,
    family: controlFamily({ kind: 'climate', metric }),
    role: 'step',
    y,
    origin: 'recorded',
    quality: 'exact',
    isAggregated: true,
  }
}

export function buildHistoryOverviewAlignedData(
  options: HistoryOverviewAlignedOptions
): AlignedData {
  const sensor = options.sensor ?? undefined
  const control = options.control ?? undefined
  const climate = options.climate ?? undefined
  const source = control ?? sensor
  if (source === undefined) return emptyOverviewData()
  const range = source.metadata.range
  if (
    sensor !== undefined &&
    control !== undefined &&
    (sensor.metadata.range.start.getTime() !== range.start.getTime() ||
      sensor.metadata.range.end.getTime() !== range.end.getTime())
  ) {
    throw new Error('overview sensor and control responses disagree about the requested range')
  }
  const buckets = historyBucketBounds(range.start, range.end, source.metadata.bucket)
  if (buckets.length === 0) return emptyOverviewData()

  const specs = manifestSeriesSpecs(options.manifest)
  const usedMetrics = new Set<string>()
  const bands: AlignedBand[] = []
  const series: AlignedSeries[] = []

  if (sensor !== undefined) {
    for (const rawSeries of sensor.series) {
      addSampledGroup(
        sensorIdentity(rawSeries, specs, usedMetrics),
        rawSeries.points,
        buckets,
        bands,
        series
      )
    }
  }
  if (control !== undefined) {
    for (const rawSeries of control.lights) {
      addSampledGroup(
        lightIdentity(rawSeries, specs, usedMetrics),
        rawSeries.points,
        buckets,
        bands,
        series
      )
    }
    if (options.includeAutomation) {
      for (const rawSeries of control.requested_pid) {
        addSampledGroup(
          pidIdentity(rawSeries, specs, usedMetrics),
          rawSeries.points,
          buckets,
          bands,
          series
        )
      }
      for (const relay of control.relays) {
        addRelayLane(relay, buckets, usedMetrics, series)
      }
    }
  }
  if (climate !== undefined && !options.includeAutomation) {
    for (const rawSeries of climate.climate) {
      const built = climateOverviewSeries(rawSeries, buckets, specs, usedMetrics)
      if (built !== null) series.push(built)
    }
  }

  return {
    x: latticeMidpoints(buckets),
    series,
    bands,
    photoperiod:
      climate === undefined || climate.photoperiod.length === 0
        ? []
        : alignPhotoperiod(climate.photoperiod, range.start.getTime(), range.end.getTime()),
    nowIndex: -1,
    nowMs: Date.now(),
    sensorIntervalSeconds: source.metadata.bucket.seconds,
    aggregated: true,
    overview: {
      buckets: buckets.map(bucket => ({
        start: bucket.start.getTime(),
        end: bucket.end.getTime(),
      })),
    },
  }
}
