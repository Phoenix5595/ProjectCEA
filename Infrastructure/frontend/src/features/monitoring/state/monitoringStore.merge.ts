/**
 * Pure merge/dedupe helpers for the monitoring store.
 *
 * These functions are side-effect free so the store class stays focused on
 * coordination. Every consumed row is deduped by an immutable source + row id
 * (series name + timestamp, or `photoperiod` + timestamp) so overlapping tail
 * pages never append duplicate points and stale rows never overwrite newer
 * ones. Aggregate resolution is preserved: points are merged as-is and never
 * re-bucketed, so raw and aggregated buckets are never mixed.
 */
import type {
  ControlMonitoringResponse,
  LiveSensorValue,
  ProjectionMetadata,
  Quality,
} from '../api'

import type { MonitoringRange } from './monitoringStore.types'

/** Serialize a `Date` to the UTC ISO string the API boundary expects. */
export function iso(d: Date): string {
  return d.toISOString()
}

export function sameRange(a: MonitoringRange, b: MonitoringRange): boolean {
  if (a.kind !== b.kind) return false
  if (a.kind === 'fixed' && b.kind === 'fixed') {
    return a.start.getTime() === b.start.getTime() && a.end.getTime() === b.end.getTime()
  }
  return a.kind === 'live' && b.kind === 'live' && a.duration === b.duration
}

/** Extract the first projection metadata from a control response, if any. */
export function extractProjection(
  resp: ControlMonitoringResponse | null,
): ProjectionMetadata | null {
  if (!resp) return null
  for (const series of [...resp.climate, ...resp.lights]) {
    if (series.projection) return series.projection
  }
  return null
}

/** Downgrade an anchor quality one step when its validity window lapses. */
export function downgradeQuality(q: Quality): Quality {
  if (q === 'exact') return 'estimated'
  return 'unavailable'
}

function mergePoints<T extends { timestamp: Date }>(
  existing: T[],
  incoming: T[],
  key: (p: T) => string,
): T[] {
  const seen = new Set(existing.map(key))
  const out = existing.slice()
  for (const p of incoming) {
    const k = key(p)
    if (seen.has(k)) continue
    seen.add(k)
    out.push(p)
  }
  out.sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
  return out
}

function mergeSeriesByName<S extends { name: string; points: { timestamp: Date }[] }>(
  existing: S[],
  incoming: S[],
): S[] {
  const byName = new Map(existing.map((s) => [s.name, s]))
  for (const s of incoming) {
    const cur = byName.get(s.name)
    if (!cur) {
      byName.set(s.name, s)
      continue
    }
    byName.set(s.name, {
      ...cur,
      points: mergePoints(cur.points, s.points, (p) => `${s.name}:${p.timestamp.getTime()}`),
    })
  }
  return [...byName.values()]
}

/** Merge a tail page into accumulated control history, deduping by row id. */
export function mergeControlHistory(
  existing: ControlMonitoringResponse,
  incoming: ControlMonitoringResponse,
): ControlMonitoringResponse {
  return {
    ...incoming,
    climate: mergeSeriesByName(existing.climate, incoming.climate),
    lights: mergeSeriesByName(existing.lights, incoming.lights),
    devices: mergeSeriesByName(existing.devices, incoming.devices),
    pid: mergeSeriesByName(existing.pid, incoming.pid),
    photoperiod: mergePoints(
      existing.photoperiod,
      incoming.photoperiod,
      (p) => `photoperiod:${p.timestamp.getTime()}`,
    ),
  }
}

/** Merge per-node live values into a single sensor-keyed snapshot. */
export function mergeLive(
  existing: LiveSensorValue[],
  incoming: LiveSensorValue[],
): LiveSensorValue[] {
  const bySensor = new Map(existing.map((v) => [v.sensor, v]))
  for (const v of incoming) bySensor.set(v.sensor, v)
  return [...bySensor.values()]
}
