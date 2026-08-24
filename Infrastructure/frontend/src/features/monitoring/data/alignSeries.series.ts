/**
 * Per-series alignment helpers for the monitoring data layer.
 *
 * Each helper maps one source series onto the shared x grid, producing a y
 * array with exactly one entry per x slot. `null` marks a gap and is never
 * interpolated. When the grid is aggregated (rebucketed), sensor and control
 * points are combined per bucket keeping min/max/average; step setpoints hold
 * forward and linear ramps are sampled at each slot.
 */
import type { PhotoperiodTimelinePoint, SensorSeries } from '../api'
import type {
  NormControlSeries,
  NormLinear,
  NormPoint,
  NormStep,
  PhotoperiodInterval,
} from './alignSeries.types'

interface SensorBuckets {
  mean: (number | null)[]
  min: (number | null)[]
  max: (number | null)[]
}

/** Align a sensor series: mean line plus min/max envelope. */
export function alignSensor(
  s: SensorSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
): SensorBuckets {
  const mean: (number | null)[] = new Array(x.length).fill(null)
  const min: (number | null)[] = new Array(x.length).fill(null)
  const max: (number | null)[] = new Array(x.length).fill(null)
  if (!aggregated) {
    const byT = new Map(s.points.map((p) => [p.timestamp.getTime(), p]))
    for (let i = 0; i < x.length; i++) {
      const p = byT.get(x[i])
      if (p) {
        mean[i] = p.average
        min[i] = p.minimum
        max[i] = p.maximum
      }
    }
    return { mean, min, max }
  }
  const buckets = new Array(x.length)
    .fill(null)
    .map(() => ({ count: 0, sum: 0, min: Infinity, max: -Infinity }))
  for (const p of s.points) {
    const t = p.timestamp.getTime()
    if (t < start || t > end) continue
    const b = buckets[bucketIndex(t, x)]
    b.count += p.sample_count
    b.sum += p.average * p.sample_count
    b.min = Math.min(b.min, p.minimum)
    b.max = Math.max(b.max, p.maximum)
  }
  for (let i = 0; i < x.length; i++) {
    const b = buckets[i]
    if (b.count > 0) {
      mean[i] = b.sum / b.count
      min[i] = b.min
      max[i] = b.max
    }
  }
  return { mean, min, max }
}

/** Align recorded/projected control points, preserving null gaps. */
export function alignControlPoints(
  cs: NormControlSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
): (number | null)[] {
  const y: (number | null)[] = new Array(x.length).fill(null)
  if (!aggregated) {
    const byT = new Map(cs.points.map((p) => [p.t, p]))
    for (let i = 0; i < x.length; i++) {
      const p = byT.get(x[i])
      if (p) y[i] = p.value
    }
    return y
  }
  const buckets = new Array(x.length)
    .fill(null)
    .map(() => ({ count: 0, sum: 0 }))
  for (const p of cs.points) {
    if (p.value === null) continue
    if (p.t < start || p.t > end) continue
    const b = buckets[bucketIndex(p.t, x)]
    b.count++
    b.sum += p.value
  }
  for (let i = 0; i < x.length; i++) {
    const b = buckets[i]
    if (b.count > 0) y[i] = b.sum / b.count
  }
  return y
}

/** Align step setpoints: each value holds forward until the next step. */
export function alignSteps(
  steps: NormStep[],
  x: number[],
  _start: number,
  _end: number,
  _aggregated: boolean,
): (number | null)[] {
  const y: (number | null)[] = new Array(x.length).fill(null)
  const sorted = [...steps].sort((a, b) => a.t - b.t)
  let idx = 0
  for (let i = 0; i < x.length; i++) {
    const slot = x[i]
    while (idx < sorted.length - 1 && sorted[idx + 1].t <= slot) idx++
    if (idx < sorted.length && sorted[idx].t <= slot) y[i] = sorted[idx].value
  }
  return y
}

export function alignDeviceStates(
  steps: NormStep[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
): (number | null)[] {
  return alignSteps(steps, x, start, end, aggregated)
}

export function alignPid(
  points: NormPoint[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean,
): (number | null)[] {
  return alignControlPoints(
    { points, metric: '', name: '', kind: 'climate', steps: [], linear: [], seriesOrigin: 'recorded', seriesQuality: 'exact', seriesIsAggregated: false },
    x,
    start,
    end,
    aggregated,
  )
}

/** Align linear ramps: sample the active segment at each x slot. */
export function alignLinear(
  linear: NormLinear[],
  x: number[],
  _start: number,
  _end: number,
  _aggregated: boolean,
): (number | null)[] {
  const y: (number | null)[] = new Array(x.length).fill(null)
  for (let i = 0; i < x.length; i++) {
    const t = x[i]
    for (const ln of linear) {
      if (t >= ln.start && t <= ln.end) {
        const frac = ln.end === ln.start ? 0 : (t - ln.start) / (ln.end - ln.start)
        y[i] = ln.startValue + frac * (ln.endValue - ln.startValue)
        break
      }
    }
  }
  return y
}

export function alignPhotoperiod(
  photoperiod: PhotoperiodTimelinePoint[],
  start: number,
  end: number,
): PhotoperiodInterval[] {
  const sorted = [...photoperiod].sort((a, b) => a.timestamp.getTime() - b.timestamp.getTime())
  const intervals: PhotoperiodInterval[] = []
  let curPhase: 'SUN' | 'MOON' | 'UNKNOWN' | null = null
  let curStart = start

  const pushInterval = (phase: 'SUN' | 'MOON' | 'UNKNOWN', from: number, to: number): void => {
    if (to <= from) return
    intervals.push({ start: from, end: to, phase })
  }

  for (const p of sorted) {
    const t = p.timestamp.getTime()
    if (t < start) {
      curPhase = p.phase
      continue
    }
    if (t > end) break
    if (curPhase === null) {
      if (t > start) {
        pushInterval('UNKNOWN', start, t)
      }
      curStart = t
      curPhase = p.phase
    } else {
      pushInterval(curPhase, curStart, t)
      curStart = t
      curPhase = p.phase
    }
  }

  if (curPhase === null) {
    pushInterval('UNKNOWN', start, end)
  } else if (end > curStart) {
    pushInterval(curPhase, curStart, end)
  }
  return intervals
}

/** Largest index whose x value is <= `t` (bucket containing `t`). */
export function bucketIndex(t: number, x: number[]): number {
  let lo = 0
  let hi = x.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (x[mid] <= t) lo = mid
    else hi = mid - 1
  }
  return lo
}
