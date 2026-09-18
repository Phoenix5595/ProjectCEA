import type { ClimatePeriod } from '../../../types/climatePeriod'
import { minutesToTime, timeToMinutes } from '../../../utils/timeMath'
import { sampleMetricSeries } from '../../../utils/climatePeriodTimeline'

export type ValueMetric = 'heating' | 'cooling' | 'vpd' | 'co2'
export type BoundaryEdge = 'start' | 'end'

export const BOUNDARY_SNAP_MINUTES = 5
export const DAY_WINDOW_MINUTES = 1440

/** Product validation ranges per metric family (temperature 10–35 °C, VPD 0–5 kPa, CO₂ 400–2000 ppm). */
export const PRODUCT_RANGES: Record<ValueMetric, { readonly min: number; readonly max: number }> = {
  heating: { min: 10, max: 35 },
  cooling: { min: 10, max: 35 },
  vpd: { min: 0, max: 5 },
  co2: { min: 400, max: 2000 },
}

export const VALUE_SNAP: Record<ValueMetric, number> = {
  heating: 0.1,
  cooling: 0.1,
  vpd: 0.1,
  co2: 10,
}

const METRIC_FIELD: Record<ValueMetric, keyof ClimatePeriod> = {
  heating: 'heating_setpoint',
  cooling: 'cooling_setpoint',
  vpd: 'vpd_setpoint',
  co2: 'co2_setpoint',
}

export function rangeMessage(metric: ValueMetric): string {
  const range = PRODUCT_RANGES[metric]
  const unit = metric === 'co2' ? 'ppm' : metric === 'vpd' ? 'kPa' : '°C'
  const label = metric === 'heating' ? 'Heating setpoint' : metric === 'cooling' ? 'Cooling setpoint' : metric === 'vpd' ? 'VPD setpoint' : 'CO₂ setpoint'
  return `${label} must stay within ${range.min}–${range.max} ${unit}.`
}

export function snapMinutes(minutes: number): number {
  return Math.round(minutes / BOUNDARY_SNAP_MINUTES) * BOUNDARY_SNAP_MINUTES
}

export function snapValue(metric: ValueMetric, raw: number): number {
  if (metric === 'co2') return Math.round(raw / VALUE_SNAP.co2) * VALUE_SNAP.co2
  return Math.round(raw * 10) / 10
}

export function isValueInRange(metric: ValueMetric, value: number): boolean {
  const range = PRODUCT_RANGES[metric]
  return value >= range.min && value <= range.max
}

/** UTC minutes-of-day of an instant — the shared daily time base of the draft. */
export function minutesOfDay(instantMs: number): number {
  return Math.floor(instantMs / 60_000) % DAY_WINDOW_MINUTES
}

/** First instant at or after windowStart whose UTC time-of-day equals the given minute. */
export function timeOfDayToInstant(minute: number, windowStartMs: number): number {
  const dayOrigin = Math.floor(windowStartMs / 86_400_000) * 86_400_000
  const instant = dayOrigin + minute * 60_000
  return instant < windowStartMs ? instant + 86_400_000 : instant
}

function sortedByStart(periods: readonly ClimatePeriod[]): Array<{ index: number; start: number; end: number }> {
  return periods
    .map((period, index) => ({ index, start: timeToMinutes(period.start_time), end: timeToMinutes(period.end_time) }))
    .sort((left, right) => left.start - right.start)
}

export function clampedBoundaryMinutes(
  periods: readonly ClimatePeriod[],
  index: number,
  edge: BoundaryEdge,
  minutes: number,
): number {
  const snapped = snapMinutes(minutes)
  const bounded = Math.min(Math.max(snapped, 0), 1435)
  const order = sortedByStart(periods)
  const position = order.findIndex((entry) => entry.index === index)
  if (position === -1) return bounded
  if (edge === 'start') {
    const previous = position > 0 ? order[position - 1] : undefined
    const own = order[position]
    const lower = previous ? Math.min(previous.end, 1435) : 0
    const upper = own ? own.end : 1435
    if (lower > upper) return bounded
    return Math.min(Math.max(bounded, lower), upper)
  }
  const own = order[position]
  const next = position < order.length - 1 ? order[position + 1] : undefined
  const lower = own ? own.start : 0
  const upper = next ? Math.max(next.start, 0) : 1435
  if (lower > upper) return bounded
  return Math.min(Math.max(bounded, lower), upper)
}

export function applyBoundary(
  periods: readonly ClimatePeriod[],
  index: number,
  edge: BoundaryEdge,
  minutes: number,
): ClimatePeriod[] {
  const field = edge === 'start' ? 'start_time' : 'end_time'
  return periods.map((period, periodIndex) => periodIndex === index
    ? { ...period, [field]: minutesToTime(minutes) }
    : period)
}

export function applyValue(
  periods: readonly ClimatePeriod[],
  index: number,
  metric: ValueMetric,
  value: number,
): ClimatePeriod[] {
  const field = METRIC_FIELD[metric]
  return periods.map((period, periodIndex) => periodIndex === index
    ? { ...period, [field]: value }
    : period)
}

export interface LocalScheduledSeries {
  readonly sampleTimes: number[]
  readonly series: ReadonlyMap<ValueMetric, (number | null)[]>
}

/**
 * Transient drag-time rendering: sample the scheduled draft locally per minute
 * (same ramp math as the table's `sampleMetricSeries`) over the envelope window.
 */
export function buildLocalScheduledSeries(
  periods: readonly ClimatePeriod[],
  window: { readonly start: Date; readonly end: Date },
): LocalScheduledSeries {
  const sampleTimes = envelopeMinuteGrid(window)
  const minuteSeries = new Map<ValueMetric, (number | null)[]>([
    ['heating', sampleMetricSeries([...periods], 'heating')],
    ['cooling', sampleMetricSeries([...periods], 'cooling')],
    ['vpd', sampleMetricSeries([...periods], 'vpd')],
    ['co2', sampleMetricSeries([...periods], 'co2')],
  ])
  const dayOrigin = Math.floor(window.start.getTime() / 86_400_000) * 86_400_000
  const series = new Map<ValueMetric, (number | null)[]>()
  for (const [metric, minuteValues] of minuteSeries) {
    series.set(metric, sampleTimes.map((t) => minuteValues[minutesOfDayFromOrigin(t, dayOrigin)] ?? null))
  }
  return { sampleTimes, series }
}

function envelopeMinuteGrid(window: { readonly start: Date; readonly end: Date }): number[] {
  const times: number[] = []
  for (let t = window.start.getTime(); t < window.end.getTime(); t += 60_000) times.push(t)
  times.push(window.end.getTime())
  return times
}

function minutesOfDayFromOrigin(instantMs: number, dayOriginMs: number): number {
  return Math.floor((instantMs - dayOriginMs) / 60_000) % DAY_WINDOW_MINUTES
}

export interface RafCoalescer<T> {
  push(value: T): void
  flush(): void
  cancel(): void
}

/**
 * Coalesces bursts into at most one commit per animation frame; the latest
 * pushed value wins.
 */
export function createRafCoalescer<T>(
  commit: (value: T) => void,
  schedule: (callback: () => void) => number = (callback) => window.requestAnimationFrame(callback),
  cancelScheduled: (id: number) => void = (id) => window.cancelAnimationFrame(id),
): RafCoalescer<T> {
  let pending: { readonly value: T } | null = null
  let frame: number | null = null
  const run = (): void => {
    frame = null
    const current = pending
    pending = null
    if (current !== null) commit(current.value)
  }
  return {
    push(value: T): void {
      pending = { value }
      if (frame === null) frame = schedule(run)
    },
    flush(): void {
      if (frame !== null) {
        cancelScheduled(frame)
        frame = null
      }
      run()
    },
    cancel(): void {
      if (frame !== null) {
        cancelScheduled(frame)
        frame = null
      }
      pending = null
    },
  }
}
