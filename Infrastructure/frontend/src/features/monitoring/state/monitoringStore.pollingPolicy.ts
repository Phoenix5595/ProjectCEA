import type { MonitoringRange } from './monitoringStore.types'

const CONTROL_TAIL_OVERLAP_MS = 2_000
const CONTROL_TAIL_MAX_MS = 120_000

const SOURCE_RETRY_CADENCE_MS = {
  'sensor-history': 60_000,
  'control-history': 30_000,
  projection: 30_000,
} as const

export type PollingSource = keyof typeof SOURCE_RETRY_CADENCE_MS

export type PollingEligibility = {
  readonly currentValues: true
  readonly sensorHistory: boolean
  readonly controlTail: boolean
  readonly projection: boolean
}

export function pollingEligibility(range: MonitoringRange): PollingEligibility {
  const recordedWork = range.kind === 'live'
  return {
    currentValues: true,
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
  lastAttemptAt: Date | null,
): boolean {
  if (lastAttemptAt === null) return true
  return now.getTime() - lastAttemptAt.getTime() > SOURCE_RETRY_CADENCE_MS[source]
}
