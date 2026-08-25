/**
 * Pure control/projection data transitions for the monitoring store.
 *
 * These functions compute a new `StoreData` from a response without touching
 * store state, so the store class stays focused on coordination. Cursors and
 * projection metadata are installed atomically with the history they describe.
 */
import type {
  ControlMonitoringResponse,
  MonitoringResponse,
  ProjectionPublicationResponse,
  PhotoperiodTimelinePoint,
} from '../api'
import { mergeControlHistory } from './monitoringStore.merge'
import { projectionTimeline } from './monitoringStore.projection'
import type { StoreData } from './monitoringStore.types'

/** Install the concurrent initial range/control/projection results. */
export function applyInitial(
  sensorRange: MonitoringResponse,
  sensorStats: MonitoringResponse,
  controlRange: ControlMonitoringResponse,
  projection: ProjectionPublicationResponse,
): StoreData {
  const proj = projectionTimeline(projection)
  return {
    series: sensorRange.series,
    statistics: sensorStats.statistics,
    live: [],
    controlHistory: controlRange,
    projectionHistory: proj.history,
    photoperiod: mergePhotoperiod(controlRange.photoperiod, proj.history?.photoperiod ?? []),
    cursors: controlRange.cursors,
    projectionRevision: proj.revision,
    projectionVersion: proj.version,
    anchorFingerprint: null,
    anchorQuality: proj.quality,
    anchorValidUntil: proj.validUntil,
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
  projection: ProjectionPublicationResponse | null,
): StoreData {
  const proj = projection ? projectionTimeline(projection) : null
  const history = controlRange ?? existing.controlHistory
  return {
    series: sensorRange?.series ?? existing.series,
    statistics: sensorRange?.statistics ?? existing.statistics,
    live: existing.live,
    controlHistory: history,
    projectionHistory: proj ? proj.history : existing.projectionHistory,
    photoperiod: mergePhotoperiod(history?.photoperiod ?? [], proj?.history?.photoperiod ?? existing.projectionHistory?.photoperiod ?? []),
    cursors: controlRange?.cursors ?? existing.cursors,
    projectionRevision: proj ? proj.revision : existing.projectionRevision,
    projectionVersion: proj ? proj.version : existing.projectionVersion,
    anchorFingerprint: proj ? null : existing.anchorFingerprint,
    anchorQuality: proj ? proj.quality : existing.anchorQuality,
    anchorValidUntil: proj ? proj.validUntil : existing.anchorValidUntil,
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
    photoperiod: mergePhotoperiod(merged.photoperiod, data.projectionHistory?.photoperiod ?? []),
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
    photoperiod: mergePhotoperiod(resp.photoperiod, data.projectionHistory?.photoperiod ?? []),
    cursors: resp.cursors,
    runtimeSnapshotVersion: resp.runtime_snapshot_version,
    flushHealth: resp.flush_health,
  }
}

/** Install a projection response only when its revision/fingerprint changed. */
export function applyProjection(
  data: StoreData,
  resp: ProjectionPublicationResponse,
): { data: StoreData; changed: boolean } {
  const proj = projectionTimeline(resp)
  const changed =
    proj.revision !== data.projectionRevision ||
    proj.version !== data.projectionVersion ||
    proj.quality !== data.anchorQuality
  if (!changed) return { data, changed: false }
  return {
    data: {
      ...data,
      projectionHistory: proj.history,
      photoperiod: mergePhotoperiod(data.controlHistory?.photoperiod ?? [], proj.history?.photoperiod ?? []),
      projectionRevision: proj.revision,
      projectionVersion: proj.version,
      anchorFingerprint: null,
      anchorQuality: proj.quality,
      anchorValidUntil: proj.validUntil,
    },
    changed: true,
  }
}

function mergePhotoperiod(
  recorded: PhotoperiodTimelinePoint[],
  projected: PhotoperiodTimelinePoint[],
): PhotoperiodTimelinePoint[] {
  const byTimestamp = new Map(projected.map((point) => [point.timestamp.getTime(), point]))
  for (const point of recorded) byTimestamp.set(point.timestamp.getTime(), point)
  return [...byTimestamp.values()].sort((left, right) => left.timestamp.getTime() - right.timestamp.getTime())
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
