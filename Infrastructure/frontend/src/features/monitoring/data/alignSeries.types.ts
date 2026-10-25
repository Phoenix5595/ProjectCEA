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
import type { ChartFamily } from '../charts/options/family'
import type { SeriesSpec } from '../config'
import type { MonitoringRange } from '../state'
import type { DeviceRegistryEntry } from '../../../types/device'
import type { LiveTailBuffer } from './liveTail'


/** Everything the alignment function needs to build the shared grid. */
export interface AlignInput {
  series: SensorSeries[]
  controlHistory: ControlMonitoringResponse | null
  projectionHistory: ControlMonitoringResponse | null
  photoperiod: PhotoperiodTimelinePoint[]
  /** Latest live values; used only when no `liveTail` buffer is supplied. */
  live: LiveSensorValue[]
  /** Raw live tail appended at its own timestamps (preferred over `live`). */
  liveTail?: LiveTailBuffer
  /** Recorded sensor bucket interval in seconds; null/1 means raw. */
  sensorIntervalSeconds?: number | null
  range: MonitoringRange
  now: Date
  /** Maximum number of x slots; defaults to 5,000. */
  maxPoints?: number
  /** Canonical room-manifest series specs used to attach presentation metadata. */
  seriesSpecs?: SeriesSpec[]
  /** Panel-level default soft bounds applied when no series explicitly declares one. */
  scaleDefaults?: { unit?: UnitFamily; softMin?: number; softMax?: number }
  /** Room-filtered device identities supplying canonical light display names. */
  lightRegistry?: readonly Pick<
    DeviceRegistryEntry,
    'device_name' | 'display_name' | 'per_room_index'
  >[]
}

/** How a series should be drawn by the uPlot adapter. */
export type SeriesKind = 'sensor' | 'point' | 'step' | 'linear'

export type SeriesSource = 'sensor' | 'climate' | 'light' | 'device' | 'pid'

export type SeriesRole =
  'mean' | 'min' | 'max' | 'point' | 'step' | 'linear' | 'band' | 'state' | 'duty' | 'pid_output'

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

/** Piecewise light intensity coverage over half-open epoch-millisecond intervals. */
export interface LightTrajectorySegment {
  readonly start: number
  readonly end: number
  readonly shape: 'step' | 'linear'
  readonly startValue: number | null
  readonly endValue: number | null
  readonly origin: Origin
  readonly quality: Quality
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
  /** Overview summaries must not bridge empty historical buckets. */
  preserveGaps?: boolean
  unit?: string
  unitFamily?: UnitFamily
  /** Canonical presentation metadata, attached when manifest specs are supplied. */
  presentation?: SeriesPresentation
  /** Exact light segments used for rendering and cursor evaluation. */
  lightTrajectory?: readonly LightTrajectorySegment[]
  /**
   * Sensor-only bucket detail, one entry per x slot (R4). Each array is null
   * where the slot carries no bucket, where the bucket holds a single sample,
   * and on raw live-tail slots. Charts render the min–max band and the bucket
   * tooltip from these; setpoint/light/device/PID series never carry them.
   */
  minimum?: (number | null)[]
  maximum?: (number | null)[]
  sampleCount?: (number | null)[]
  /** Sensor-only marker: true where the slot is a raw live-tail point. */
  liveTail?: boolean[]
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
  /** Index into `x` of the last recorded/tail slot at or before `nowMs`. */
  nowIndex: number
  /** Wall-clock instant of this alignment; live follow uses it directly. */
  nowMs: number
  /** Recorded sensor bucket interval in seconds; null/1 means raw. */
  sensorIntervalSeconds: number | null
  /** True when the grid was rebucketed to stay within maxPoints. */
  aggregated: boolean
  /** Panel-level default soft bounds applied when no series explicitly declares one. */
  scaleDefaults?: { unit?: UnitFamily; softMin?: number; softMax?: number }
  /** Exact UTC bucket bounds, aligned one-to-one with overview x midpoints. */
  overview?: { readonly buckets: readonly { start: number; end: number }[] }
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
  /** Recorded extrema of the samples a source-aggregated point represents. A
   * retained mean/last is never an extremum, so the fields stay null when the
   * source does not prove them for this bucket. */
  minimum?: number | null
  maximum?: number | null
}

export interface NormStep {
  t: number
  value: number | null
  origin: Origin
  quality: Quality
  /** Whether the step's source buckets are aggregates; see NormPoint. */
  isAggregated?: boolean
  /** Recorded extrema of the samples the step represents; see NormPoint. */
  minimum?: number | null
  maximum?: number | null
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
  trajectoryKind: 'scheduled' | 'effective' | null
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
  time: Date
  value: number | null
  provenance?: { origin: Origin; quality: Quality; is_aggregated: boolean }
}

export interface RawControlStep {
  time: Date
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
  points: { time: Date; device_state: number; device_mode: string }[]
}

export interface RawPidSeries {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: { time: Date; pid_output: number | null; duty_cycle_percent: number | null }[]
}

export interface ControlSeriesBundle {
  climate: RawControlSeries[]
  lights: RawControlSeries[]
  devices: RawDeviceSeries[]
  pid: RawPidSeries[]
}
