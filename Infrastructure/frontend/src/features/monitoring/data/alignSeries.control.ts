/**
 * Control history normalization and merge logic.
 *
 * Converts climate/light timeline responses into a normalized shape keyed by
 * a stable metric, then merges recorded history with future projections so
 * recorded values win on collisions.
 */
import type { ControlMonitoringResponse, Origin, Quality } from '../api'

import type {
  NormControlSeries,
  NormDeviceSeries,
  NormLinear,
  NormPidSeries,
} from './alignSeries.types'

export function metricFromName(name: string): string {
  return name
    .toLowerCase()
    .replace(/\[[^\]]*\]/g, '')
    .replace(/\s+/g, '_')
    .replace(/[^a-z0-9_]/g, '')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
}

const LIGHT_INTENSITY_PREFIX = 'light.intensity.'

export function canonicalLightDeviceName(metricOrName: string): string {
  return metricOrName.startsWith(LIGHT_INTENSITY_PREFIX)
    ? metricOrName.slice(LIGHT_INTENSITY_PREFIX.length)
    : metricOrName
}


/** Merge only climate targets; light trajectories retain separate provenance sources. */
export function mergeControlSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null
): NormControlSeries[] {
  const byKey = new Map<string, NormControlSeries>()
  for (const response of [history, projection]) {
    if (response === null) continue
    for (const series of response.climate) {
      const metric =
        series.metric ?? series.points[0]?.metric ?? metricFromName(series.name)
      const normalized = normalizeControlSeries(series, metric, 'climate')
      const trajectoryKind =
        normalized.trajectoryKind === 'effective' ? 'effective' : 'scheduled'
      const key = `climate:${metric}:${trajectoryKind}`
      const current = byKey.get(key)
      byKey.set(key, current ? mergeControl(current, normalized) : normalized)
    }
  }
  return [...byKey.values()]
}

/** Keep recorded and selected projected representations separate per physical light. */
export function mergeLightSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null
): {
  deviceName: string
  history?: NormControlSeries
  projection?: NormControlSeries
}[] {
  const recorded = normalizeLightResponse(history)
  const projected = normalizeLightResponse(projection)
  const deviceNames = [
    ...new Set([...recorded.keys(), ...projected.keys()]),
  ].sort()
  return deviceNames.map(deviceName => {
    const historySeries = selectLightTrajectory(recorded.get(deviceName))
    const projectionSeries = selectLightTrajectory(projected.get(deviceName))
    return {
      deviceName,
      ...(historySeries === undefined ? {} : { history: historySeries }),
      ...(projectionSeries === undefined ? {} : { projection: projectionSeries }),
    }
  })
}

type LightTrajectoryKinds = Map<NormControlSeries['trajectoryKind'], NormControlSeries>

function normalizeLightResponse(
  response: ControlMonitoringResponse | null
): Map<string, LightTrajectoryKinds> {
  const byDevice = new Map<string, LightTrajectoryKinds>()
  if (response === null) return byDevice
  for (const series of response.lights) {
    const deviceName =
      series.points[0]?.device_name ??
      canonicalLightDeviceName(series.metric ?? series.name)
    if (deviceName.length === 0) continue
    const normalized = {
      ...normalizeControlSeries(series, deviceName, 'light'),
      name: deviceName,
      metric: deviceName,
    }
    let trajectories = byDevice.get(deviceName)
    if (trajectories === undefined) {
      trajectories = new Map()
      byDevice.set(deviceName, trajectories)
    }
    const current = trajectories.get(normalized.trajectoryKind)
    trajectories.set(
      normalized.trajectoryKind,
      current === undefined ? normalized : mergeLightControl(current, normalized)
    )
  }
  return byDevice
}

function selectLightTrajectory(
  trajectories: LightTrajectoryKinds | undefined
): NormControlSeries | undefined {
  return (
    trajectories?.get('effective') ??
    trajectories?.get('scheduled') ??
    trajectories?.get(null)
  )
}

function mergeLightControl(
  existing: NormControlSeries,
  incoming: NormControlSeries
): NormControlSeries {
  return {
    ...existing,
    points: mergeTargetByTime(existing.points, incoming.points),
    steps: mergeTargetByTime(existing.steps, incoming.steps),
    linear: mergeLinear(existing.linear, incoming.linear),
  }
}

/** Merge recorded history and future projection device state series by metric. */

export function mergeDeviceSeries(
  history: ControlMonitoringResponse | null,
  projection: ControlMonitoringResponse | null
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
  projection: ControlMonitoringResponse | null
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

type SharedTimeline =
  ControlMonitoringResponse['climate'][number] | ControlMonitoringResponse['lights'][number]

function normalizeControlSeries(
  s: SharedTimeline,
  metric: string,
  kind: 'climate' | 'light'
): NormControlSeries {
  return {
    name: s.name,
    metric,
    kind,
    trajectoryKind: s.trajectory_kind ?? null,
    points: canonicalizeTargetValues(
      s.points.map(p => ({
        t: p.timestamp.getTime(),
        value: p.value,
        origin: p.provenance.origin,
        quality: p.provenance.quality,
        isAggregated: p.provenance.is_aggregated,
      }))
    ),
    steps: canonicalizeTargetValues(
      s.steps.map(st => ({
        t: st.timestamp.getTime(),
        value: st.value,
        origin: st.provenance.origin,
        quality: st.provenance.quality,
      }))
    ),
    linear: s.linear.map(ln => ({
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
  points: { timestamp: Date; device_state: number }[]
}

function normalizeDeviceSeries(s: RawDeviceInput, metric: string): NormDeviceSeries {
  return {
    name: s.name,
    metric,
    states: s.points.map(p => ({
      t: p.timestamp.getTime(),
      value: p.device_state,
      origin: s.provenance.origin,
      quality: s.provenance.quality,
    })),
    seriesOrigin: s.provenance.origin,
    seriesQuality: s.provenance.quality,
    seriesIsAggregated: s.provenance.is_aggregated,
  }
}

type RawPidPoint = {
  timestamp: Date
  pid_output?: number | null
  duty_cycle_percent?: number | null
}

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
      .filter(p => p.pid_output !== null && p.pid_output !== undefined)
      .map(p => ({
        t: p.timestamp.getTime(),
        value: p.pid_output ?? null,
        origin: s.provenance.origin,
        quality: s.provenance.quality,
        isAggregated: s.provenance.is_aggregated,
      })),
    dutyCycles: s.points
      .filter(p => p.duty_cycle_percent !== null && p.duty_cycle_percent !== undefined)
      .map(p => ({
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
  const points = mergeByTime(a.points, b.points, p => p.origin === 'recorded')
  const steps = mergeByTime(a.steps, b.steps, p => p.origin === 'recorded')
  if (a.metric.endsWith('_setpoint')) {
    const targetPoints = mergeTargetByTime(a.points, b.points)
    const targetSteps = mergeTargetByTime(a.steps, b.steps)
    return {
      ...a,
      trajectoryKind: a.trajectoryKind ?? b.trajectoryKind,
      points: [],
      steps: mergeTargetByTime(
        targetSteps,
        targetPoints.map(({ t, value, origin, quality }) => ({ t, value, origin, quality }))
      ),
      linear: mergeLinear(a.linear, b.linear),
    }
  }
  return {
    ...a,
    trajectoryKind: a.trajectoryKind ?? b.trajectoryKind,
    points,
    steps,
    linear: mergeLinear(a.linear, b.linear),
  }
}

function mergeDevice(a: NormDeviceSeries, b: NormDeviceSeries): NormDeviceSeries {
  return {
    ...a,
    states: mergeByTime(a.states, b.states, p => p.origin === 'recorded'),
  }
}

function mergePid(a: NormPidSeries, b: NormPidSeries): NormPidSeries {
  return {
    ...a,
    pidOutputs: mergeByTime(a.pidOutputs, b.pidOutputs, p => p.origin === 'recorded'),
    dutyCycles: mergeByTime(a.dutyCycles, b.dutyCycles, p => p.origin === 'recorded'),
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

function canonicalizeTargetValues<T extends { t: number; value: number | null }>(values: T[]): T[] {
  const byTimestamp = new Map<number, T>()
  for (const value of values) {
    const current = byTimestamp.get(value.t)
    if (!current || (Number.isFinite(value.value) && !Number.isFinite(current.value))) {
      byTimestamp.set(value.t, value)
    }
  }
  return [...byTimestamp.values()].sort((left, right) => left.t - right.t)
}

function mergeTargetByTime<T extends { t: number; value: number | null; origin: Origin }>(
  a: T[],
  b: T[]
): T[] {
  const byTimestamp = new Map<number, T>()
  for (const value of [...a, ...b]) {
    const current = byTimestamp.get(value.t)
    if (!current) {
      byTimestamp.set(value.t, value)
      continue
    }
    if (current.origin !== value.origin) {
      if (value.origin === 'recorded') byTimestamp.set(value.t, value)
      continue
    }
    if (Number.isFinite(value.value) && !Number.isFinite(current.value)) {
      byTimestamp.set(value.t, value)
    }
  }
  return [...byTimestamp.values()].sort((left, right) => left.t - right.t)
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
