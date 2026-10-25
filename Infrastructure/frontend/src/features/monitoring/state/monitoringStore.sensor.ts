/**
 * Sensor-history transitions for the monitoring store (R4).
 *
 * Recorded sensor responses are the base of the live view. In live mode only
 * completed buckets enter that base; the newest in-progress bucket arrives
 * through the raw live tail instead and is replaced once the incremental
 * refresh returns its completed bucket. Fixed (Historical) ranges keep every
 * bucket the backend returned.
 */
import type { LiveSensorValue, MonitoringResponse, SensorSeries } from '../api'

import { mergeLive } from './monitoringStore.merge'
import type { StoreData } from './monitoringStore.types'

/** A bucket is complete once this grace period has passed since generation. */
const BUCKET_SETTLE_MS = 2_000

export interface SensorApplyContext {
  /** True for a rolling live range; false for a fixed Historical range. */
  readonly live: boolean
  /** Wall-clock instant of the response completion. */
  readonly nowMs: number
  /** Visible live duration in milliseconds (unused for fixed ranges). */
  readonly durationMs: number
}

function intervalMsOf(intervalSeconds: number | null): number {
  return intervalSeconds === null ? 1_000 : Math.max(1, intervalSeconds) * 1_000
}

function trimPoints(series: SensorSeries, cutoff: number): SensorSeries {
  const first = series.points[0]
  if (first === undefined || first.timestamp.getTime() >= cutoff) return series
  return {
    ...series,
    points: series.points.filter(point => point.timestamp.getTime() >= cutoff),
  }
}

/**
 * Drop the bucket that was still in progress when the response was generated
 * and report the first instant not covered by a completed bucket.
 */
function completeBuckets(
  response: MonitoringResponse,
  intervalMs: number
): { series: SensorSeries[]; baseCompleteUntil: number | null } {
  const cutoff = response.metadata.generated_at.getTime() - BUCKET_SETTLE_MS
  let lastKept = Number.NEGATIVE_INFINITY
  const series = response.series.map(candidate => {
    const points = candidate.points.filter(
      point => point.timestamp.getTime() + intervalMs <= cutoff
    )
    const last = points[points.length - 1]
    if (last !== undefined) lastKept = Math.max(lastKept, last.timestamp.getTime())
    return points.length === candidate.points.length ? candidate : { ...candidate, points }
  })
  return {
    series,
    baseCompleteUntil: Number.isFinite(lastKept) ? lastKept + intervalMs : null,
  }
}

/**
 * Replace the recorded sensor base from a full-range response. In live mode
 * the newest in-progress bucket is withheld and the raw tail baseline resets.
 */
export function applySensorRange(
  existing: StoreData,
  response: MonitoringResponse,
  context: SensorApplyContext
): StoreData {
  const intervalSeconds = response.metadata.interval_seconds ?? null
  const sensorIntervalSeconds = intervalSeconds
  if (!context.live) {
    existing.liveTail.clear()
    return {
      ...existing,
      series: response.series,
      statistics: response.statistics,
      sensorIntervalSeconds,
    }
  }
  const intervalMs = intervalMsOf(intervalSeconds)
  const complete = completeBuckets(response, intervalMs)
  const cutoff = context.nowMs - context.durationMs
  existing.liveTail.reset(complete.baseCompleteUntil, intervalMs)
  return {
    ...existing,
    series: complete.series.map(series => trimPoints(series, cutoff)),
    statistics: response.statistics,
    sensorIntervalSeconds,
  }
}

/**
 * Apply an incremental response: completed buckets at or after `start`
 * replace the matching base points, the baseline advances, and points older
 * than the visible window are trimmed. Statistics are deliberately untouched
 * so the statistics table keeps its last full-range values.
 */
export function applySensorIncremental(
  existing: StoreData,
  response: MonitoringResponse,
  context: SensorApplyContext
): StoreData {
  const baseline = existing.liveTail.baseCompleteUntil
  if (baseline === null) return existing
  const intervalSeconds = existing.sensorIntervalSeconds
  const intervalMs = intervalMsOf(intervalSeconds)
  const start = Math.floor((baseline - 2 * intervalMs) / intervalMs) * intervalMs
  const cutoff = context.nowMs - context.durationMs
  const complete = completeBuckets(response, intervalMs)
  if (complete.baseCompleteUntil === null) return existing

  const bySensor = new Map(complete.series.map(series => [series.sensor, series]))
  const known = new Set(existing.series.map(series => series.sensor))
  const series = existing.series.map(candidate => {
    const retained = candidate.points.filter(point => {
      const timestamp = point.timestamp.getTime()
      return timestamp >= cutoff && timestamp < start
    })
    const replacement = bySensor.get(candidate.sensor)
    if (replacement === undefined) {
      return retained.length === candidate.points.length ? candidate : { ...candidate, points: retained }
    }
    const incoming = replacement.points.filter(point => point.timestamp.getTime() >= start)
    return {
      ...candidate,
      points: [...retained, ...incoming].sort(
        (left, right) => left.timestamp.getTime() - right.timestamp.getTime()
      ),
    }
  })
  for (const candidate of complete.series) {
    if (known.has(candidate.sensor)) continue
    series.push({
      ...candidate,
      points: candidate.points.filter(point => point.timestamp.getTime() >= start),
    })
  }
  existing.liveTail.advance(complete.baseCompleteUntil)
  return { ...existing, series, sensorIntervalSeconds: intervalSeconds }
}

/**
 * Record the newest live values: the latest snapshot feeds the value tables
 * and every sample enters the raw tail (at most one point per bucket).
 */
export function applyLiveValues(existing: StoreData, values: LiveSensorValue[]): StoreData {
  if (values.length === 0) return existing
  existing.liveTail.pushMany(values)
  return { ...existing, live: mergeLive(existing.live, values) }
}
