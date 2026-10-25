/**
 * Per-series alignment helpers for the monitoring data layer.
 *
 * Each helper maps one source series onto the shared x grid, producing a y
 * array with exactly one entry per x slot. `null` marks a gap and is never
 * interpolated. When the grid is aggregated (rebucketed), sensor and control
 * points are combined per bucket keeping min/max/average; step setpoints hold
 * forward and linear ramps are sampled at each slot. Aligners that pair a
 * scalar value with known per-slot extrema (minimum/maximum) return a
 * `ScalarAlignment` so the caller can emit the matching envelope without a
 * diverging second pass.
 */
import type { PhotoperiodTimelinePoint, SensorSeries } from '../api'

import type {
  NormControlSeries,
  NormLinear,
  NormPoint,
  NormStep,
  PhotoperiodInterval,
} from './alignSeries.types'

export interface SensorBuckets {
  mean: (number | null)[]
  min: (number | null)[]
  max: (number | null)[]
  /** Samples represented by each slot; null where the slot has no bucket. */
  count: (number | null)[]
}

/** A scalar value array aligned to the shared grid plus the known extrema of
 * the samples each slot represents (`null` where an extremum is unknown). */
export interface ScalarAlignment {
  y: (number | null)[]
  min: (number | null)[]
  max: (number | null)[]
}

function finiteBound(value: number | null | undefined): number | null {
  return value === null || value === undefined || !Number.isFinite(value) ? null : value
}

/**
 * Known extrema one sample contributes to a slot. A source-aggregated sample
 * offers only its recorded minimum/maximum — its retained value is a mean or
 * last and is never itself an extremum; a raw sample is its own extremum.
 * Unknown sides stay null so an envelope never invents a zero or reuses the
 * retained value as a phantom bound.
 */
function sampleBounds(sample: {
  value: number | null
  isAggregated?: boolean
  minimum?: number | null
  maximum?: number | null
}): { low: number | null; high: number | null } {
  if (sample.isAggregated === true) {
    return { low: finiteBound(sample.minimum), high: finiteBound(sample.maximum) }
  }
  const value = finiteBound(sample.value)
  return { low: value, high: value }
}

/** Fold one known sample bound into the per-slot extrema. Unknown bounds
 * (`null`) contribute nothing and never fabricate a finite extremum. */
function foldBound(
  target: (number | null)[],
  slot: number,
  bound: number | null,
  fold: (a: number, b: number) => number
): void {
  if (bound === null) return
  const known = target[slot]
  target[slot] = known === null ? bound : fold(known, bound)
}

/** Align a sensor series: mean line plus min/max envelope. */
export function alignSensor(
  s: SensorSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): SensorBuckets {
  const mean: (number | null)[] = new Array(x.length).fill(null)
  const min: (number | null)[] = new Array(x.length).fill(null)
  const max: (number | null)[] = new Array(x.length).fill(null)
  const count: (number | null)[] = new Array(x.length).fill(null)
  if (!aggregated) {
    const byT = new Map(s.points.map(p => [p.timestamp.getTime(), p]))
    for (let i = 0; i < x.length; i++) {
      const p = byT.get(x[i])
      if (p) {
        mean[i] = p.average
        min[i] = p.minimum
        max[i] = p.maximum
        count[i] = p.sample_count
      }
    }
    return { mean, min, max, count }
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
      count[i] = b.count
    }
  }
  return { mean, min, max, count }
}

/**
 * Align recorded/projected control points, preserving null gaps, together with
 * the known extrema of every sample each slot represents. When the client
 * reduces several samples into one slot, source-aggregated samples keep their
 * recorded minimum/maximum and raw samples contribute their own value, so a
 * hidden peak still surfaces in the envelope.
 */
export function alignControlPoints(
  cs: NormControlSeries,
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): ScalarAlignment {
  const y: (number | null)[] = new Array(x.length).fill(null)
  const min: (number | null)[] = new Array(x.length).fill(null)
  const max: (number | null)[] = new Array(x.length).fill(null)
  if (!aggregated) {
    const byT = new Map(cs.points.map(p => [p.t, p]))
    for (let i = 0; i < x.length; i++) {
      const p = byT.get(x[i])
      if (p === undefined) continue
      y[i] = p.value
      const { low, high } = sampleBounds(p)
      min[i] = low
      max[i] = high
    }
    return { y, min, max }
  }
  const buckets = new Array(x.length).fill(null).map(() => ({ count: 0, sum: 0 }))
  const lows = new Array<number | null>(x.length).fill(null)
  const highs = new Array<number | null>(x.length).fill(null)
  for (const p of cs.points) {
    if (p.t < start || p.t > end) continue
    if (p.value !== null) {
      const bucket = buckets[bucketIndex(p.t, x)]
      bucket.count++
      bucket.sum += p.value
    }
    const { low, high } = sampleBounds(p)
    const slot = bucketIndex(p.t, x)
    foldBound(lows, slot, low, Math.min)
    foldBound(highs, slot, high, Math.max)
  }
  for (let i = 0; i < x.length; i++) {
    const bucket = buckets[i]
    if (bucket.count > 0) y[i] = bucket.sum / bucket.count
    min[i] = lows[i]
    max[i] = highs[i]
  }
  return { y, min, max }
}

/**
 * Align step setpoints as the held-forward value plus the known extrema of the
 * samples each slot represents. Without a rebucket the envelope mirrors the
 * in-effect step's own bounds. With a rebucket, the in-effect step's bounds
 * keep the band aligned with the held line and every step starting inside the
 * slot's span contributes its bounds, so a transient peak hidden by the hold
 * still surfaces.
 */
export function alignStepsWithBounds(
  steps: NormStep[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): ScalarAlignment {
  const y: (number | null)[] = new Array(x.length).fill(null)
  const min: (number | null)[] = new Array(x.length).fill(null)
  const max: (number | null)[] = new Array(x.length).fill(null)
  const lows = new Array<number | null>(x.length).fill(null)
  const highs = new Array<number | null>(x.length).fill(null)
  const sorted = [...steps].sort((a, b) => a.t - b.t)

  // The held step supplies both the rendered value and its recorded bounds in
  // the same scan, so the envelope can never drift from the drawn line.
  let idx = 0
  for (let i = 0; i < x.length; i++) {
    const slot = x[i]
    while (idx < sorted.length - 1 && sorted[idx + 1].t <= slot) idx++
    const held = idx < sorted.length && sorted[idx].t <= slot ? sorted[idx] : undefined
    if (held === undefined) continue
    y[i] = held.value
    const { low, high } = sampleBounds(held)
    if (!aggregated) {
      min[i] = low
      max[i] = high
      continue
    }
    foldBound(lows, i, low, Math.min)
    foldBound(highs, i, high, Math.max)
  }

  if (aggregated) {
    // Every step starting inside a slot's span also represents itself there:
    // this is where a hold-forward rendering would otherwise hide it.
    for (const step of steps) {
      if (step.t < start || step.t > end) continue
      const { low, high } = sampleBounds(step)
      const slot = bucketIndex(step.t, x)
      foldBound(lows, slot, low, Math.min)
      foldBound(highs, slot, high, Math.max)
    }
    for (let i = 0; i < x.length; i++) {
      min[i] = lows[i]
      max[i] = highs[i]
    }
  }
  return { y, min, max }
}

/** Align step setpoints: each value holds forward until the next step. */
export function alignSteps(
  steps: NormStep[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): (number | null)[] {
  return alignStepsWithBounds(steps, x, start, end, aggregated).y
}

export function alignDeviceStates(
  steps: NormStep[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): (number | null)[] {
  return alignSteps(steps, x, start, end, aggregated)
}

export function alignPid(
  points: NormPoint[],
  x: number[],
  start: number,
  end: number,
  aggregated: boolean
): ScalarAlignment {
  return alignControlPoints(
    {
      points,
      metric: '',
      name: '',
      kind: 'climate',
      trajectoryKind: null,
      steps: [],
      linear: [],
      seriesOrigin: 'recorded',
      seriesQuality: 'exact',
      seriesIsAggregated: false,
    },
    x,
    start,
    end,
    aggregated
  )
}

/** Align linear ramps: sample the active segment at each x slot. */
export function alignLinear(
  linear: NormLinear[],
  x: number[],
  _start: number,
  _end: number,
  _aggregated: boolean
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
  end: number
): PhotoperiodInterval[] {
  return alignPhotoperiodWithPredecessorFloor(
    photoperiod,
    start,
    end,
    Number.NEGATIVE_INFINITY
  )
}

function alignPhotoperiodWithPredecessorFloor(
  photoperiod: PhotoperiodTimelinePoint[],
  start: number,
  end: number,
  predecessorFloor: number
): PhotoperiodInterval[] {
  const sorted = [...photoperiod].sort((a, b) => a.time.getTime() - b.time.getTime())
  const intervals: PhotoperiodInterval[] = []
  let curPhase: 'SUN' | 'MOON' | 'UNKNOWN' | null = null
  let curStart = start

  const pushInterval = (phase: 'SUN' | 'MOON' | 'UNKNOWN', from: number, to: number): void => {
    if (to <= from) return
    intervals.push({ start: from, end: to, phase })
  }

  for (const p of sorted) {
    const t = p.time.getTime()
    if (t < start) {
      if (t >= predecessorFloor) curPhase = p.phase
      continue
    }
    // A point at `end` belongs to the next half-open window, not this one.
    if (t >= end) break
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

/**
 * Compose historical phase evidence and the independent future publication
 * over the visible half-open window. Historical evidence stops at Now and its
 * response end; projected evidence starts no earlier than Now and is confined
 * to its publication window. Unsupported spans remain explicitly UNKNOWN.
 */
export function composePhotoperiod(
  recorded: PhotoperiodTimelinePoint[],
  projected: PhotoperiodTimelinePoint[],
  recordedEnd: number,
  projectedStart: number,
  projectedEnd: number,
  start: number,
  end: number,
  now: number
): PhotoperiodInterval[] {
  if (end <= start) return []

  const output: PhotoperiodInterval[] = []
  let cursor = start
  const appendOutput = (interval: PhotoperiodInterval): void => {
    const previous = output[output.length - 1]
    if (
      previous !== undefined &&
      previous.end === interval.start &&
      previous.phase === interval.phase
    ) {
      previous.end = interval.end
    } else {
      output.push(interval)
    }
  }
  const appendSource = (source: PhotoperiodInterval[]): void => {
    for (const interval of source) {
      if (interval.end <= cursor || interval.start >= end) continue
      const intervalStart = Math.max(cursor, interval.start)
      const intervalEnd = Math.min(end, interval.end)
      if (intervalStart > cursor) {
        appendOutput({ start: cursor, end: intervalStart, phase: 'UNKNOWN' })
      }
      if (intervalEnd <= intervalStart) continue
      appendOutput({ start: intervalStart, end: intervalEnd, phase: interval.phase })
      cursor = intervalEnd
    }
  }

  const historyEnd = Math.min(end, now, recordedEnd)
  if (start < historyEnd) appendSource(alignPhotoperiod(recorded, start, historyEnd))

  const projectionFrom = Math.max(start, now, projectedStart)
  const projectionTo = Math.min(end, projectedEnd)
  if (projectionFrom < projectionTo) {
    // A predecessor may seed the projected span only if it is within this
    // publication's own half-open window.
    appendSource(
      alignPhotoperiodWithPredecessorFloor(
        projected,
        projectionFrom,
        projectionTo,
        projectedStart
      )
    )
  }

  if (cursor < end) appendOutput({ start: cursor, end, phase: 'UNKNOWN' })
  return output
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
