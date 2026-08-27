/**
 * Types for the monitoring data-alignment layer.
 *
 * `alignSeries` consumes the Todo 20 store shape (sensor series, recorded
 * control history, future projection, photoperiod, live) plus the active range
 * and `now`, and produces one shared sorted-unique UTC x axis with per-series y
 * arrays aligned to it. All timestamps are `Date` objects already normalized
 * once by the Todo 19 API boundary; this layer treats them as opaque time
 * values and never re-parses them.
 */
import type {
  ControlMonitoringResponse,
  LiveSensorValue,
  Origin,
  PhotoperiodTimelinePoint,
  Quality,
  SensorSeries,
  UnitFamily,
} from '../api'
import type { SeriesSpec } from '../config'
import type { MonitoringRange } from '../state'
import type { ChartFamily } from '../charts/options/family'

/** Everything the alignment function needs to build the shared grid. */
export interface AlignInput {
  series: SensorSeries[]
  controlHistory: ControlMonitoringResponse | null
  projectionHistory: ControlMonitoringResponse | null
  photoperiod: PhotoperiodTimelinePoint[]
  live: LiveSensorValue[]
  range: MonitoringRange
  now: Date
  /** Maximum number of x slots; defaults to 5,000. */
  maxPoints?: number
  /** Canonical room-manifest series specs used to attach presentation metadata. */
  seriesSpecs?: SeriesSpec[]
  /** Panel-level default soft bounds applied when no series explicitly declares one. */
  scaleDefaults?: { unit?: UnitFamily; softMin?: number; softMax?: number }
}

/** How a series should be drawn by the uPlot adapter. */
export type SeriesKind = 'sensor' | 'point' | 'step' | 'linear'

export type SeriesSource = 'sensor' | 'climate' | 'light' | 'device' | 'pid'

export type SeriesRole =
  | 'mean'
  | 'min'
  | 'max'
  | 'point'
  | 'step'
  | 'linear'
  | 'band'
  | 'state'
  | 'duty'
  | 'pid_output'

export type SeriesKey = string & { readonly __seriesKey: unique symbol }

export function seriesKey(source: SeriesSource, metric: string, role: SeriesRole): SeriesKey {
  return `${source}:${metric}:${role}` as SeriesKey
}

/**
 * Immutable presentation metadata for one aligned series, sourced from the
 * matching room-manifest `SeriesSpec`. uPlot series option builders consume
 * `color`, `dash`, `lineWidth`, and `decimals`; scale builders consume
 * `softMin`/`softMax`.
 */
export interface SeriesPresentation {
  readonly label?: string
  readonly color?: string
  readonly dash?: readonly number[]
  readonly lineWidth?: number
  readonly decimals?: number
  readonly softMin?: number
  readonly softMax?: number
}

/** Mutable builder used while constructing a frozen `SeriesPresentation`. */
export type MutableSeriesPresentation = {
  -readonly [K in keyof SeriesPresentation]?: SeriesPresentation[K]
}

/** One y array aligned to the shared x axis. */
export interface AlignedSeries {
  key: SeriesKey
  label: string
  kind: SeriesKind
  source: SeriesSource
  metric: string
  node?: string
  family: ChartFamily
  role: SeriesRole
  /** One entry per x slot; `null` marks a gap (never interpolated). */
  y: (number | null)[]
  origin: Origin
  quality: Quality
  isAggregated: boolean
  unit?: string
  unitFamily?: UnitFamily
  /** Canonical presentation metadata, attached when manifest specs are supplied. */
  presentation?: SeriesPresentation
}

/** A min/max envelope pair referencing two emitted series keys. */
export interface AlignedBand {
  key: SeriesKey
  minKey: SeriesKey
  maxKey: SeriesKey
}

export interface PhotoperiodInterval {
  start: number
  end: number
  phase: 'SUN' | 'MOON' | 'UNKNOWN'
}

/** The complete aligned output ready for the uPlot adapter. */
export interface AlignedData {
  /** Shared sorted-unique epoch-ms x axis, length <= maxPoints. */
  x: number[]
  series: AlignedSeries[]
  bands: AlignedBand[]
  photoperiod: PhotoperiodInterval[]
  /** Index into `x` where the "now" divider falls. */
  nowIndex: number
  /** True when the grid was rebucketed to stay within maxPoints. */
  aggregated: boolean
  /** Panel-level default soft bounds applied when no series explicitly declares one. */
  scaleDefaults?: { unit?: UnitFamily; softMin?: number; softMax?: number }
}

// ---------------------------------------------------------------------------
// Internal normalized control-series shapes (never exported from the barrel)
// ---------------------------------------------------------------------------

export interface NormPoint {
  t: number
  value: number | null
  origin: Origin
  quality: Quality
  isAggregated: boolean
}

export interface NormStep {
  t: number
  value: number | null
  origin: Origin
  quality: Quality
}

export interface NormLinear {
  start: number
  end: number
  startValue: number
  endValue: number
  origin: Origin
  quality: Quality
}

export interface NormControlSeries {
  name: string
  metric: string
  kind: 'climate' | 'light'
  points: NormPoint[]
  steps: NormStep[]
  linear: NormLinear[]
  seriesOrigin: Origin
  seriesQuality: Quality
  seriesIsAggregated: boolean
}

export interface NormDeviceSeries {
  name: string
  metric: string
  states: NormStep[]
  duties: NormPoint[]
  seriesOrigin: Origin
  seriesQuality: Quality
  seriesIsAggregated: boolean
}

export interface NormPidSeries {
  name: string
  metric: string
  pidOutputs: NormPoint[]
  dutyCycles: NormPoint[]
  seriesOrigin: Origin
  seriesQuality: Quality
  seriesIsAggregated: boolean
}

export interface RawControlPoint {
  timestamp: Date
  value: number | null
  provenance?: { origin: Origin; quality: Quality; is_aggregated: boolean }
}

export interface RawControlStep {
  timestamp: Date
  value: number | null
  provenance?: { origin: Origin; quality: Quality }
}

export interface RawControlLinear {
  start: Date
  end: Date
  start_value: number
  end_value: number
  provenance?: { origin: Origin; quality: Quality }
}

export interface RawControlSeries {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: RawControlPoint[]
  steps: RawControlStep[]
  linear: RawControlLinear[]
}

export interface RawDeviceSeries {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: { timestamp: Date; device_state: number; device_mode: string }[]
}

export interface RawPidSeries {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: { timestamp: Date; pid_output: number | null; duty_cycle_percent: number | null }[]
}

export interface ControlSeriesBundle {
  climate: RawControlSeries[]
  lights: RawControlSeries[]
  devices: RawDeviceSeries[]
  pid: RawPidSeries[]
}
