/**
 * Control history normalization and merge logic.
 *
 * Converts climate/light timeline responses into a normalized shape keyed by
 * a stable metric, then merges recorded history with future projections so
 * recorded values win on collisions.
 */
import type { ControlMonitoringResponse, Origin, Quality } from '../api'
import type { NormControlSeries, NormDeviceSeries, NormLinear, NormPidSeries } from './alignSeries.types'

export function metricFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
}

/** Merge recorded history and future projection control series by metric. */
export function mergeControlSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null,
): NormControlSeries[] {
  const byKey = new Map<string, NormControlSeries>()
  const add = (resp: ControlMonitoringResponse | null, kind: 'climate' | 'light'): void => {
    if (!resp) return
    const list = kind === 'climate' ? resp.climate : resp.lights
    for (const s of list) {
      const metric = metricFromName(s.name)
      const norm = normalizeControlSeries(s as unknown as SharedTimeline, metric, kind)
      const key = `${kind}:${metric}`
      const cur = byKey.get(key)
      byKey.set(key, cur ? mergeControl(cur, norm) : norm)
    }
  }
  add(history, 'climate')
  add(history, 'light')
  add(projection, 'climate')
  add(projection, 'light')
  return [...byKey.values()]
}

export function mergeDeviceSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null,
): NormDeviceSeries[] {
  const byKey = new Map<string, NormDeviceSeries>()
  const add = (resp: ControlMonitoringResponse | null): void => {
    if (!resp) return
    for (const s of resp.devices) {
      const metric = metricFromName(s.name)
      const norm = normalizeDeviceSeries(s, metric)
      const key = `device:${metric}`
      const cur = byKey.get(key)
      byKey.set(key, cur ? mergeDevice(cur, norm) : norm)
    }
  }
  add(history)
  add(projection)
  return [...byKey.values()]
}

export function mergePidSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null,
): NormPidSeries[] {
  const byKey = new Map<string, NormPidSeries>()
  const add = (resp: ControlMonitoringResponse | null): void => {
    if (!resp) return
    for (const s of resp.pid) {
      const metric = metricFromName(s.name)
      const norm = normalizePidSeries(s, metric)
      const key = `pid:${metric}`
      const cur = byKey.get(key)
      byKey.set(key, cur ? mergePid(cur, norm) : norm)
    }
  }
  add(history)
  add(projection)
  return [...byKey.values()]
}

interface SharedTimeline {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: { timestamp: Date; value: number | null; provenance: { origin: Origin; quality: Quality; is_aggregated: boolean } }[]
  steps: { timestamp: Date; value: number | null; provenance: { origin: Origin; quality: Quality } }[]
  linear: { start: Date; end: Date; start_value: number; end_value: number; provenance: { origin: Origin; quality: Quality } }[]
}

function normalizeControlSeries(
  s: SharedTimeline,
  metric: string,
  kind: 'climate' | 'light',
): NormControlSeries {
  return {
    name: s.name,
    metric,
    kind,
    points: s.points.map((p) => ({
      t: p.timestamp.getTime(),
      value: p.value,
      origin: p.provenance.origin,
      quality: p.provenance.quality,
      isAggregated: p.provenance.is_aggregated,
    })),
    steps: s.steps.map((st) => ({
      t: st.timestamp.getTime(),
      value: st.value,
      origin: st.provenance.origin,
      quality: st.provenance.quality,
    })),
    linear: s.linear.map((ln) => ({
      start: ln.start.getTime(),
      end: ln.end.getTime(),
      startValue: ln.start_value,
      endValue: ln.end_value,
      origin: ln.provenance.origin,
      quality: ln.provenance.quality,
    })),
    seriesOrigin: s.provenance.origin,
    seriesQuality: s.provenance.quality,
    seriesIsAggregated: s.provenance.is_aggregated,
  }
}

type RawDeviceInput = {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: { timestamp: Date; device_state: number; device_mode: string }[]
}

function normalizeDeviceSeries(s: RawDeviceInput, metric: string): NormDeviceSeries {
  return {
    name: s.name,
    metric,
    states: s.points.map((p) => ({
      t: p.timestamp.getTime(),
      value: p.device_state,
      origin: s.provenance.origin,
      quality: s.provenance.quality,
    })),
    duties: s.points
      .filter((p) => p.device_mode === 'AUTO' || p.device_mode === 'MANUAL')
      .map((p) => ({
        t: p.timestamp.getTime(),
        value: p.device_state,
        origin: s.provenance.origin,
        quality: s.provenance.quality,
        isAggregated: s.provenance.is_aggregated,
      })),
    seriesOrigin: s.provenance.origin,
    seriesQuality: s.provenance.quality,
    seriesIsAggregated: s.provenance.is_aggregated,
  }
}

type RawPidPoint = { timestamp: Date; pid_output?: number | null; duty_cycle_percent?: number | null }

type RawPidInput = {
  name: string
  provenance: { origin: Origin; quality: Quality; is_aggregated: boolean }
  points: RawPidPoint[]
}

function normalizePidSeries(s: RawPidInput, metric: string): NormPidSeries {
  return {
    name: s.name,
    metric: `${metric}_pid`,
    pidOutputs: s.points
      .filter((p) => p.pid_output !== null && p.pid_output !== undefined)
      .map((p) => ({
        t: p.timestamp.getTime(),
        value: p.pid_output ?? null,
        origin: s.provenance.origin,
        quality: s.provenance.quality,
        isAggregated: s.provenance.is_aggregated,
      })),
    dutyCycles: s.points
      .filter((p) => p.duty_cycle_percent !== null && p.duty_cycle_percent !== undefined)
      .map((p) => ({
        t: p.timestamp.getTime(),
        value: p.duty_cycle_percent ?? null,
        origin: s.provenance.origin,
        quality: s.provenance.quality,
        isAggregated: s.provenance.is_aggregated,
      })),
    seriesOrigin: s.provenance.origin,
    seriesQuality: s.provenance.quality,
    seriesIsAggregated: s.provenance.is_aggregated,
  }
}

function mergeControl(a: NormControlSeries, b: NormControlSeries): NormControlSeries {
  return {
    ...a,
    points: mergeByTime(a.points, b.points, (p) => p.origin === 'recorded'),
    steps: mergeByTime(a.steps, b.steps, (p) => p.origin === 'recorded'),
    linear: mergeLinear(a.linear, b.linear),
  }
}

function mergeDevice(a: NormDeviceSeries, b: NormDeviceSeries): NormDeviceSeries {
  return {
    ...a,
    states: mergeByTime(a.states, b.states, (p) => p.origin === 'recorded'),
    duties: mergeByTime(a.duties, b.duties, (p) => p.origin === 'recorded'),
  }
}

function mergePid(a: NormPidSeries, b: NormPidSeries): NormPidSeries {
  return {
    ...a,
    pidOutputs: mergeByTime(a.pidOutputs, b.pidOutputs, (p) => p.origin === 'recorded'),
    dutyCycles: mergeByTime(a.dutyCycles, b.dutyCycles, (p) => p.origin === 'recorded'),
  }
}

function mergeByTime<T extends { t: number }>(a: T[], b: T[], prefer: (x: T) => boolean): T[] {
  const map = new Map<number, T>()
  for (const x of a) map.set(x.t, x)
  for (const x of b) {
    const cur = map.get(x.t)
    if (!cur || prefer(x)) map.set(x.t, x)
  }
  return [...map.values()].sort((p, q) => p.t - q.t)
}

function mergeLinear(a: NormLinear[], b: NormLinear[]): NormLinear[] {
  const map = new Map<string, NormLinear>()
  for (const x of a) map.set(`${x.start}:${x.end}`, x)
  for (const x of b) {
    const k = `${x.start}:${x.end}`
    const cur = map.get(k)
    if (!cur || x.origin === 'recorded') map.set(k, x)
  }
  return [...map.values()].sort((p, q) => p.start - q.start)
}
