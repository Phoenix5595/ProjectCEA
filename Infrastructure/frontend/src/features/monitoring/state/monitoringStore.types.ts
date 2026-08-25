/**
 * Monitoring store state shapes.
 *
 * The store distinguishes a rolling `LiveRange` (a fixed-duration window
 * anchored to `now`) from an absolute `FixedRange` (`[start, end)`). All
 * timestamps are `Date` objects already normalized once by the Todo 19 API
 * boundary; downstream code treats them as opaque time values and never
 * re-parses them.
 */
import type {
  ControlMonitoringResponse,
  FlushHealth,
  LiveSensorValue,
  PhotoperiodTimelinePoint,
  Quality,
  SensorSeries,
  SensorStatistics,
  SourceCursor,
} from '../api'

/** A rolling live window anchored to `now` with a fixed duration (ms). */
export interface LiveRange {
  readonly kind: 'live'
  readonly duration: number
}

/** A fixed absolute half-open window `[start, end)`. */
export interface FixedRange {
  readonly kind: 'fixed'
  readonly start: Date
  readonly end: Date
}

export type MonitoringRange = LiveRange | FixedRange

/** Immutable accumulated monitoring data held by the store. */
export interface StoreData {
  series: SensorSeries[]
  statistics: SensorStatistics[]
  live: LiveSensorValue[]
  controlHistory: ControlMonitoringResponse | null
  projectionHistory: ControlMonitoringResponse | null
  photoperiod: PhotoperiodTimelinePoint[]
  cursors: SourceCursor[]
  projectionRevision: string | null
  anchorFingerprint: string | null
  anchorQuality: Quality | null
  anchorValidUntil: Date | null
  runtimeSnapshotVersion: number | null
  flushHealth: FlushHealth[]
}

/** The complete externally-visible store snapshot. */
export interface StoreState {
  range: MonitoringRange
  isLive: boolean
  data: StoreData
  loading: boolean
  tailLoading: boolean
  reconciling: boolean
  errors: string[]
  lastGoodRangeAt: Date | null
  rangeErrorAt: Date | null
}

/** Construction options for `MonitoringStore`. */
export interface MonitoringStoreOptions {
  pollIntervalMs?: number
  now?: () => Date
}
