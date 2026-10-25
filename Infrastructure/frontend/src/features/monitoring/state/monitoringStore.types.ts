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
import type { DeviceRegistryEntry } from '../../../types/device'
import type { LiveTailBuffer } from '../data/liveTail'

import type { MonitoringSourceOutcomes } from './monitoringStore.health'

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

export interface FulfilledRange {
  readonly range: MonitoringRange
  readonly start: Date
  readonly end: Date
  readonly revision: number
}

/** Immutable accumulated monitoring data held by the store. */
export interface StoreData {
  series: SensorSeries[]
  statistics: SensorStatistics[]
  live: LiveSensorValue[]
  /** Raw live tail (≤ one point per bucket) appended at its own timestamps. */
  liveTail: LiveTailBuffer
  /** Recorded sensor bucket interval in seconds; null means raw/unknown. */
  sensorIntervalSeconds: number | null
  controlHistory: ControlMonitoringResponse | null
  projectionHistory: ControlMonitoringResponse | null
  /** Recorded historical room-photoperiod points only; the projection
   * publication's own phase list stays in `projectionHistory` and is composed
   * at alignment time, so a projection refresh never recolors the past. */
  photoperiod: PhotoperiodTimelinePoint[]
  cursors: SourceCursor[]
  projectionRevision: string | null
  projectionVersion: number | null
  anchorFingerprint: string | null
  anchorQuality: Quality | null
  anchorValidUntil: Date | null
  runtimeSnapshotVersion: number | null
  flushHealth: FlushHealth[]
  /** Delayed canonical display names of the room lights (labeling only). */
  lightRegistry?: readonly Pick<
    DeviceRegistryEntry,
    'device_name' | 'display_name' | 'per_room_index'
  >[]
}

/** The complete externally-visible store snapshot. */
export interface StoreState {
  range: MonitoringRange
  fulfilledRange: FulfilledRange | null
  isLive: boolean
  data: StoreData
  readonly sourceOutcomes: MonitoringSourceOutcomes
  loading: boolean
  tailLoading: boolean
  reconciling: boolean
  errors: string[]
  lastGoodRangeAt: Date | null
  rangeErrorAt: Date | null
  /** Wall-clock instant the live view was paused; the live viewport freezes there. */
  pausedAt?: Date | null
}

/** Which page owns this store: Monitoring (live + projection) or Historical. */
export type MonitoringStoreMode = 'monitoring' | 'historical'

/** Construction options for `MonitoringStore`. */
export interface MonitoringStoreOptions {
  pollIntervalMs?: number
  now?: () => Date
  loadDeviceRegistry?: () => Promise<DeviceRegistryEntry[]>
  /** Historical stores never load a projection or poll live sources. */
  mode?: MonitoringStoreMode
  /** Live duration the Now button restores before any live range was shown. */
  defaultLiveDurationMs?: number
}
