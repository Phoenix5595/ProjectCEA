/**
 * Pure control/projection data transitions for the monitoring store.
 *
 * These functions compute a new `StoreData` from a response without touching
 * store state, so the store class stays focused on coordination. Cursors and
 * projection metadata are installed atomically with the history they describe.
 */
import type {
  ControlMonitoringResponse,
  MonitoringResponse
} from '../api'
import { extractProjection, mergeControlHistory } from './monitoringStore.merge'
import type { StoreData } from './monitoringStore.types'

/** Install the concurrent initial range/control/projection results. */
export function applyInitial(
  sensorRange: MonitoringResponse,
  sensorStats: MonitoringResponse,
  controlRange: ControlMonitoringResponse,
  projection: ControlMonitoringResponse,
): StoreData {
  const proj = extractProjection(projection)
  return {
    series: sensorRange.series,
    statistics: sensorStats.statistics,
    live: [],
    controlHistory: controlRange,
    projectionHistory: projection,
    photoperiod: controlRange.photoperiod,
    cursors: controlRange.cursors,
    projectionRevision: proj?.projection_revision ?? null,
    anchorFingerprint: proj?.anchor_fingerprint ?? null,
    anchorQuality: proj?.anchor_quality ?? null,
    anchorValidUntil: proj?.anchor_valid_until ?? null,
    runtimeSnapshotVersion: controlRange.runtime_snapshot_version,
    flushHealth: controlRange.flush_health,
  }
}

/**
 * Install partial initial results, preserving last-good data for any source
 * that failed. Each nullable response falls back to the existing value so a
 * per-service outage never clears sibling panels or recorded history.
 */
export function applyInitialPartial(
  existing: StoreData,
  sensorRange: MonitoringResponse | null,
  controlRange: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null,
): StoreData {
  const proj = projection ? extractProjection(projection) : null
  return {
    series: sensorRange?.series ?? existing.series,
    statistics: sensorRange?.statistics ?? existing.statistics,
    live: existing.live,
    controlHistory: controlRange ?? existing.controlHistory,
    projectionHistory: projection ?? existing.projectionHistory,
    photoperiod: controlRange?.photoperiod ?? existing.photoperiod,
    cursors: controlRange?.cursors ?? existing.cursors,
    projectionRevision: proj?.projection_revision ?? existing.projectionRevision,
    anchorFingerprint: proj?.anchor_fingerprint ?? existing.anchorFingerprint,
    anchorQuality: proj?.anchor_quality ?? existing.anchorQuality,
    anchorValidUntil: proj?.anchor_valid_until ?? existing.anchorValidUntil,
    runtimeSnapshotVersion:
      controlRange?.runtime_snapshot_version ?? existing.runtimeSnapshotVersion,
    flushHealth: controlRange?.flush_health ?? existing.flushHealth,
  }
}

/** Merge a tail page into accumulated history, deduping by row id. */
export function applyControl(data: StoreData, resp: ControlMonitoringResponse): StoreData {
  const history = data.controlHistory
  const merged = history ? mergeControlHistory(history, resp) : resp
  return {
    ...data,
    controlHistory: merged,
    photoperiod: merged.photoperiod,
    cursors: resp.cursors,
    runtimeSnapshotVersion: resp.runtime_snapshot_version,
    flushHealth: resp.flush_health,
  }
}

/** Replace history and cursors wholesale (used by reconciliation). */
export function applyControlFresh(data: StoreData, resp: ControlMonitoringResponse): StoreData {
  return {
    ...data,
    controlHistory: resp,
    photoperiod: resp.photoperiod,
    cursors: resp.cursors,
    runtimeSnapshotVersion: resp.runtime_snapshot_version,
    flushHealth: resp.flush_health,
  }
}

/** Install a projection response only when its revision/fingerprint changed. */
export function applyProjection(
  data: StoreData,
  resp: ControlMonitoringResponse,
): { data: StoreData; changed: boolean } {
  const proj = extractProjection(resp)
  if (proj === null) return { data, changed: false }
  const changed =
    proj.projection_revision !== data.projectionRevision ||
    proj.anchor_fingerprint !== data.anchorFingerprint
  if (!changed) return { data, changed: false }
  return {
    data: {
      ...data,
      projectionHistory: resp,
      projectionRevision: proj.projection_revision,
      anchorFingerprint: proj.anchor_fingerprint,
      anchorQuality: proj.anchor_quality,
      anchorValidUntil: proj.anchor_valid_until,
    },
    changed: true,
  }
}

/** True when wall-clock has passed the projection anchor validity deadline. */
export function projectionExpired(data: StoreData, now: Date): boolean {
  if (!data.anchorValidUntil) return false
  return now.getTime() >= data.anchorValidUntil.getTime()
}

/** True when a previously-unhealthy flush source reports healthy again. */
export function flushHealthRecovered(
  prev: StoreData,
  resp: ControlMonitoringResponse,
): boolean {
  const prevHealthy = new Map(prev.flushHealth.map((f) => [f.source, f.healthy]))
  return resp.flush_health.some((f) => prevHealthy.get(f.source) === false && f.healthy === true)
}


/** Most recent recorded timestamp across all control envelope sections. */
export function lastControlTimestamp(data: StoreData): Date | null {
  const history = data.controlHistory
  if (!history) return null
  let last: Date | null = null
  const scan = (points: readonly { timestamp: Date }[]): void => {
    for (const point of points) {
      if (last === null || point.timestamp > last) last = point.timestamp
    }
  }
  for (const series of [...history.climate, ...history.lights]) scan(series.points)
  for (const series of [...history.devices, ...history.pid]) scan(series.points)
  scan(history.photoperiod)
  return last
}
