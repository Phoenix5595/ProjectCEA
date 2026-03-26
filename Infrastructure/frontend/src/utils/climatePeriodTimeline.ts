import type { ClimatePeriod } from '../types/climatePeriod'
import { timeToMinutes } from './timeMath'

export { timeToMinutes }

/** Exclusive end semantics: [start, end) or wrap [start,1440) U [0,end). */
export function isMinuteInPeriod(minute: number, startMin: number, endMin: number): boolean {
  if (startMin < endMin) {
    return minute >= startMin && minute < endMin
  }
  if (startMin > endMin) {
    return minute >= startMin || minute < endMin
  }
  return false
}

/** Distance in minutes from period start along the period window (before wrap). */
export function offsetInPeriod(minute: number, startMin: number, endMin: number): number {
  if (startMin < endMin) {
    return minute - startMin
  }
  if (startMin > endMin) {
    if (minute >= startMin) return minute - startMin
    return 1440 - startMin + minute
  }
  return 0
}

export function periodLengthMinutes(startMin: number, endMin: number): number {
  if (startMin < endMin) return endMin - startMin
  if (startMin > endMin) return 1440 - startMin + endMin
  return 0
}

export function sortPeriodsByStart(periods: ClimatePeriod[]): ClimatePeriod[] {
  return [...periods].sort((a, b) => timeToMinutes(a.start_time) - timeToMinutes(b.start_time))
}

function lerp(a: number | null, b: number | null, t: number): number | null {
  if (b == null && a == null) return null
  if (b == null) return a
  if (a == null) return b
  return a + (b - a) * t
}

export type SetpointMetric = 'heating' | 'cooling' | 'vpd'

function pickMetric(p: ClimatePeriod, metric: SetpointMetric): number | null {
  if (metric === 'heating') return p.heating_setpoint
  if (metric === 'cooling') return p.cooling_setpoint
  return p.vpd_setpoint
}

/**
 * Sample one metric for each minute 0..1439 (control-aligned ramp at period start).
 */
export function sampleMetricSeries(
  periods: ClimatePeriod[],
  metric: SetpointMetric
): (number | null)[] {
  const out: (number | null)[] = new Array(1440).fill(null)
  if (!periods.length) return out

  const sorted = sortPeriodsByStart(periods)
  const n = sorted.length

  for (let m = 0; m < 1440; m++) {
    let idx = -1
    for (let i = 0; i < n; i++) {
      const s = timeToMinutes(sorted[i].start_time)
      const e = timeToMinutes(sorted[i].end_time)
      if (isMinuteInPeriod(m, s, e)) {
        idx = i
        break
      }
    }
    if (idx < 0) continue

    const cur = sorted[idx]
    const prev = sorted[(idx - 1 + n) % n]
    const s = timeToMinutes(cur.start_time)
    const e = timeToMinutes(cur.end_time)
    const len = periodLengthMinutes(s, e)
    const ramp = Math.min(Math.max(0, cur.ramp_minutes), len > 0 ? len : 0)
    const offset = offsetInPeriod(m, s, e)

    const nominal = pickMetric(cur, metric)
    const prevVal = pickMetric(prev, metric)

    if (ramp <= 0 || offset >= ramp) {
      out[m] = nominal
    } else {
      const t = ramp > 0 ? offset / ramp : 1
      out[m] = lerp(prevVal, nominal, t)
    }
  }

  return out
}

export function seriesMinMax(values: (number | null)[]): { min: number; max: number } | null {
  let min = Infinity
  let max = -Infinity
  for (const v of values) {
    if (v == null || Number.isNaN(v)) continue
    min = Math.min(min, v)
    max = Math.max(max, v)
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null
  if (min === max) {
    return { min: min - 1, max: max + 1 }
  }
  return { min, max }
}

/** Padding for axis (°C vs kPa). */
export function paddedRange(
  min: number,
  max: number,
  padFraction: number
): { min: number; max: number } {
  const span = max - min
  const pad = span * padFraction || 0.5
  return { min: min - pad, max: max + pad }
}
