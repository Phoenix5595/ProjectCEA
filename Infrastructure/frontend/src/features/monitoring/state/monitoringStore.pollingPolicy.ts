import type { MonitoringRange, MonitoringStoreMode } from './monitoringStore.types'

const CONTROL_TAIL_OVERLAP_MS = 2_000
const CONTROL_TAIL_MAX_MS = 120_000

/** Earliest spacing between two sensor-history refreshes in live mode. */
export const LIVE_SENSOR_MIN_INTERVAL_MS = 5_000
/** Extra wait after a completed bucket before asking for the next one. */
export const LIVE_SENSOR_BUCKET_SLACK_MS = 3_000
/** Periodic full reload (series + statistics) of a live sensor range. */
export const LIVE_SENSOR_FULL_RELOAD_MS = 15 * 60_000
/** Fixed-range recorded-read retry cadence: 5 s, 15 s, then every 60 s. */
const FIXED_RETRY_CADENCE_MS = [5_000, 15_000, 60_000] as const

const SOURCE_RETRY_CADENCE_MS = {
  'sensor-history': 60_000,
  'control-history': 30_000,
  projection: 30_000,
} as const

export type PollingSource = keyof typeof SOURCE_RETRY_CADENCE_MS

export type PollingEligibility = {
  readonly currentValues: boolean
  readonly sensorHistory: boolean
  readonly controlTail: boolean
  readonly projection: boolean
}

export function pollingEligibility(
  range: MonitoringRange,
  mode: MonitoringStoreMode = 'monitoring'
): PollingEligibility {
  const historical = mode === 'historical'
  const recordedWork = !historical && range.kind === 'live'
  return {
    currentValues: !historical,
    sensorHistory: recordedWork,
    controlTail: recordedWork,
    projection: recordedWork,
  }
}

export function controlTailStart(now: Date, last: Date | null): Date {
  // A control anchor ahead of the local clock (server clock skew) would send
  // start >= end, which the monitoring API rejects as an invalid interval.
  const minimumStart = now.getTime() - CONTROL_TAIL_MAX_MS
  const overlapStart = last === null ? minimumStart : last.getTime() - CONTROL_TAIL_OVERLAP_MS
  return new Date(Math.max(Math.min(overlapStart, now.getTime() - 1), minimumStart))
}

export function isSourceRetryEligible(
  source: PollingSource,
  now: Date,
  lastAttemptAt: Date | null
): boolean {
  if (lastAttemptAt === null) return true
  return now.getTime() - lastAttemptAt.getTime() > SOURCE_RETRY_CADENCE_MS[source]
}

/** Delay before the next automatic retry of a failed fixed-range read. */
export function fixedRangeRetryDelay(failures: number): number {
  if (failures <= 0) return 0
  return FIXED_RETRY_CADENCE_MS[Math.min(failures - 1, FIXED_RETRY_CADENCE_MS.length - 1)] ?? 60_000
}

/** Earliest instant the next incremental sensor refresh may start. */
export function nextSensorIncrementalAt(
  lastRefreshAt: number,
  baseCompleteUntil: number,
  intervalMs: number
): number {
  return Math.max(
    lastRefreshAt + LIVE_SENSOR_MIN_INTERVAL_MS,
    baseCompleteUntil + intervalMs + LIVE_SENSOR_BUCKET_SLACK_MS
  )
}

/** Overlapped incremental start, aligned down to the bucket interval. */
export function sensorIncrementalStart(baseCompleteUntil: number, intervalMs: number): number {
  return Math.floor((baseCompleteUntil - 2 * intervalMs) / intervalMs) * intervalMs
}
