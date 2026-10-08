/**
 * Pure merge/dedupe helpers for the monitoring store.
 *
 * These functions are side-effect free so the store class stays focused on
 * coordination. Every consumed row is deduped by an immutable source + row id
 * (series name + timestamp) so overlapping tail pages never append duplicate
 * points and stale rows never overwrite newer ones. Aggregate resolution is
 * preserved: points are merged as-is and never re-bucketed, so raw and
 * aggregated buckets are never mixed. `photoperiod` is treated as recorded
 * history: its incoming response replaces the existing resident points inside
 * its own half-open range instead of merging by bare timestamp.
 */
import type {
  ControlMonitoringResponse,
  LiveSensorValue,
  PhotoperiodTimelinePoint,
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
  resp: ControlMonitoringResponse | null
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
  key: (p: T) => string
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
  incoming: S[]
): S[] {
  const byName = new Map(existing.map(s => [s.name, s]))
  for (const s of incoming) {
    const cur = byName.get(s.name)
    if (!cur) {
      byName.set(s.name, s)
      continue
    }
    byName.set(s.name, {
      ...cur,
      points: mergePoints(cur.points, s.points, p => `${s.name}:${p.timestamp.getTime()}`),
    })
  }
  return [...byName.values()]
}

function mergeTargetValues<T extends { timestamp: Date; value: number | null }>(
  existing: T[],
  incoming: T[]
): T[] {
  const byTimestamp = new Map<number, T>()
  for (const value of [...existing, ...incoming]) {
    const timestamp = value.timestamp.getTime()
    const current = byTimestamp.get(timestamp)
    if (!current || (Number.isFinite(value.value) && !Number.isFinite(current.value))) {
      byTimestamp.set(timestamp, value)
    }
  }
  return [...byTimestamp.values()].sort(
    (left, right) => left.timestamp.getTime() - right.timestamp.getTime()
  )
}

function mergeTargetSeriesByName<
  S extends {
    name: string
    points: { timestamp: Date; value: number | null }[]
    steps: { timestamp: Date; value: number | null }[]
  },
>(existing: S[], incoming: S[]): S[] {
  const byName = new Map(existing.map(series => [series.name, series]))
  for (const series of incoming) {
    const current = byName.get(series.name)
    if (!current) {
      byName.set(series.name, series)
      continue
    }
    byName.set(series.name, {
      ...current,
      points: mergeTargetValues(current.points, series.points),
      steps: mergeTargetValues(current.steps, series.steps),
    })
  }
  return [...byName.values()]
}

function mergeLightSeriesByIdentity(
  existing: ControlMonitoringResponse['lights'],
  incoming: ControlMonitoringResponse['lights']
): ControlMonitoringResponse['lights'] {
  const byKey = new Map<string, ControlMonitoringResponse['lights'][number]>()
  for (const series of [...existing, ...incoming]) {
    const deviceName = canonicalLightDeviceName(series)
    const trajectoryKind = series.trajectory_kind ?? null
    const key = `${deviceName}:${trajectoryKind ?? ''}`
    const current = byKey.get(key)
    if (current === undefined) {
      byKey.set(key, { ...series, name: deviceName, metric: deviceName })
      continue
    }
    byKey.set(key, {
      ...current,
      name: deviceName,
      metric: deviceName,
      trajectory_kind: trajectoryKind,
      points: mergeTargetValues(current.points, series.points),
      steps: mergeTargetValues(current.steps, series.steps),
      linear: mergeLinearSegments(current.linear, series.linear),
    })
  }
  return [...byKey.values()]
}

function canonicalLightDeviceName(
  series: ControlMonitoringResponse['lights'][number]
): string {
  const point = series.points[0]
  if (point !== undefined) return point.device_name
  const metric = series.metric ?? series.name
  const prefix = 'light.intensity.'
  return metric.startsWith(prefix) ? metric.slice(prefix.length) : metric
}

function mergeLinearSegments<T extends { start: Date; end: Date }>(
  existing: T[],
  incoming: T[]
): T[] {
  const byInterval = new Map<string, T>()
  for (const segment of [...existing, ...incoming]) {
    const key = `${segment.start.getTime()}:${segment.end.getTime()}`
    if (!byInterval.has(key)) byInterval.set(key, segment)
  }
  return [...byInterval.values()].sort((left, right) => left.start.getTime() - right.start.getTime())
}


/** Merge a tail page into accumulated control history, deduping by row id. */
export function mergeControlHistory(
  existing: ControlMonitoringResponse,
  incoming: ControlMonitoringResponse
): ControlMonitoringResponse {
  return {
    ...incoming,
    climate: mergeTargetSeriesByName(existing.climate, incoming.climate),
    lights: mergeLightSeriesByIdentity(existing.lights, incoming.lights),
    devices: mergeSeriesByName(existing.devices, incoming.devices),
    pid: mergeSeriesByName(existing.pid, incoming.pid),
    photoperiod: mergeHistoricalPhotoperiod(
      existing.photoperiod,
      incoming.photoperiod,
      incoming.range.start.getTime(),
      incoming.range.end.getTime()
    ),
  }
}

function photoperiodSignature(point: PhotoperiodTimelinePoint): string {
  return [
    point.phase,
    point.provenance.origin,
    point.provenance.quality,
    String(point.provenance.is_aggregated),
    point.mode_id ?? null,
    point.submode_id ?? null,
    point.runtime_snapshot_version ?? null,
  ].join('|')
}

/**
 * Merge recorded photoperiod history. The incoming response replaces the
 * existing points inside its own half-open range `[start, end)` and points
 * outside that range are preserved verbatim, so newly committed evidence
 * replaces an earlier placeholder inside the response window while the rest of
 * the resident timeline stays untouched. Adjacent points whose full
 * signature (phase + provenance + metadata) agrees are collapsed so a repeated
 * synthetic carry-in anchor cannot inflate the resident timeline.
 */
function mergeHistoricalPhotoperiod(
  existing: PhotoperiodTimelinePoint[],
  incoming: PhotoperiodTimelinePoint[],
  start: number,
  end: number
): PhotoperiodTimelinePoint[] {
  const outside: PhotoperiodTimelinePoint[] = []
  for (const point of existing) {
    const timestamp = point.timestamp.getTime()
    if (timestamp < start || timestamp >= end) outside.push(point)
  }
  const inside = new Map<number, PhotoperiodTimelinePoint>()
  for (const point of incoming) {
    const timestamp = point.timestamp.getTime()
    if (timestamp >= start && timestamp < end) inside.set(timestamp, point)
  }
  const merged: PhotoperiodTimelinePoint[] = [...outside, ...inside.values()].sort(
    (left, right) => left.timestamp.getTime() - right.timestamp.getTime()
  )
  const collapsed: PhotoperiodTimelinePoint[] = []
  for (const point of merged) {
    const previous = collapsed[collapsed.length - 1]
    if (previous !== undefined && photoperiodSignature(previous) === photoperiodSignature(point)) {
      continue
    }
    collapsed.push(point)
  }
  return collapsed
}

/** Merge per-node live values into a single sensor-keyed snapshot. */
export function mergeLive(
  existing: LiveSensorValue[],
  incoming: LiveSensorValue[]
): LiveSensorValue[] {
  const bySensor = new Map(existing.map(v => [v.sensor, v]))
  for (const v of incoming) bySensor.set(v.sensor, v)
  return [...bySensor.values()]
}
