import type { ClimatePeriod } from '../../../types/climatePeriod'
import { rampSkipThreshold } from '../../../utils/climatePeriodTimeline'
import { minutesToTime, timeToMinutes } from '../../../utils/timeMath'
import {
  scheduleClockInstant,
  scheduleOccurrences,
  scheduleLocalDates,
  scheduleNextLocalDate,
} from './scheduleClock'

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

const METRIC_FIELD: Record<
  ValueMetric,
  'heating_setpoint' | 'cooling_setpoint' | 'vpd_setpoint' | 'co2_setpoint'
> = {
  heating: 'heating_setpoint',
  cooling: 'cooling_setpoint',
  vpd: 'vpd_setpoint',
  co2: 'co2_setpoint',
}

export function rangeMessage(metric: ValueMetric): string {
  const range = PRODUCT_RANGES[metric]
  const unit = metric === 'co2' ? 'ppm' : metric === 'vpd' ? 'kPa' : '°C'
  const label =
    metric === 'heating'
      ? 'Heating setpoint'
      : metric === 'cooling'
        ? 'Cooling setpoint'
        : metric === 'vpd'
          ? 'VPD setpoint'
          : 'CO₂ setpoint'
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

/**
 * First instant at or after windowStart whose Toronto schedule clock equals
 * the given minute (the window-start local date's occurrence, or the next
 * local date when that one is earlier). DST folds resolve to the first
 * occurrence and spring gaps to the forward-shifted clock, per the shared
 * recurring resolver; null means the clock does not resolve — never a UTC
 * fallback.
 */
export function timeOfDayToInstant(minute: number, windowStartMs: number): number | null {
  const clamped =
    ((Math.floor(minute) % DAY_WINDOW_MINUTES) + DAY_WINDOW_MINUTES) % DAY_WINDOW_MINUTES
  const [first] = scheduleOccurrences(
    windowStartMs,
    windowStartMs + 36 * 3_600_000,
    minutesToTime(clamped)
  )
  return first === undefined ? null : first
}

function sortedByStart(
  periods: readonly ClimatePeriod[]
): Array<{ index: number; start: number; end: number }> {
  return periods
    .map((period, index) => ({
      index,
      start: timeToMinutes(period.start_time),
      end: timeToMinutes(period.end_time),
    }))
    .sort((left, right) => left.start - right.start)
}

export function clampedBoundaryMinutes(
  periods: readonly ClimatePeriod[],
  index: number,
  edge: BoundaryEdge,
  minutes: number
): number {
  const snapped = snapMinutes(minutes)
  const bounded = Math.min(Math.max(snapped, 0), 1435)
  const order = sortedByStart(periods)
  const position = order.findIndex(entry => entry.index === index)
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
  minutes: number
): ClimatePeriod[] {
  const field = edge === 'start' ? 'start_time' : 'end_time'
  return periods.map((period, periodIndex) =>
    periodIndex === index ? { ...period, [field]: minutesToTime(minutes) } : period
  )
}

export function applyValue(
  periods: readonly ClimatePeriod[],
  index: number,
  metric: ValueMetric,
  value: number
): ClimatePeriod[] {
  const field = METRIC_FIELD[metric]
  return periods.map((period, periodIndex) =>
    periodIndex === index ? { ...period, [field]: value } : period
  )
}

export interface LocalScheduledSeries {
  readonly sampleTimes: readonly number[]
  readonly series: ReadonlyMap<ValueMetric, (number | null)[]>
}

interface LocalOccurrence {
  readonly periodIndex: number
  readonly predecessorIndex: number
  readonly startMs: number
  readonly endMs: number
}

const LOCAL_METRICS: readonly ValueMetric[] = ['heating', 'cooling', 'vpd', 'co2']

/**
 * Resolved absolute occurrences of the draft's stored Toronto clocks across
 * the window's local dates (including the preceding date so overnight and
 * all-day periods open the window). Equal start/end clocks are all-day
 * occurrences running to the same clock on the next local date. Each
 * occurrence carries its cyclic predecessor period (same profile) for ramp
 * seeding.
 */
function localScheduleOccurrences(
  periods: readonly ClimatePeriod[],
  startMs: number,
  endMs: number
): LocalOccurrence[] {
  const order = periods
    .map((period, index) => ({ index, startMin: timeToMinutes(period.start_time) }))
    .sort((left, right) => left.startMin - right.startMin)
  const occurrences: LocalOccurrence[] = []
  for (const localDate of scheduleLocalDates(startMs, endMs)) {
    for (let position = 0; position < order.length; position += 1) {
      const entry = order[position]
      const period = periods[entry.index]
      const occurrenceStart = scheduleClockInstant(localDate, period.start_time)
      if (occurrenceStart === null) continue
      const endMin = timeToMinutes(period.end_time)
      const endDate =
        entry.startMin >= endMin ? scheduleNextLocalDate(localDate) : localDate
      const occurrenceEnd = endDate === null ? null : scheduleClockInstant(endDate, period.end_time)
      if (occurrenceEnd === null || occurrenceEnd <= occurrenceStart) continue
      // The window is the clip authority: an occurrence never renders at the
      // window end, while a ramp started before the window keeps its true
      // origin for elapsed minutes.
      const clippedEnd = Math.min(occurrenceEnd, endMs)
      if (clippedEnd <= occurrenceStart) continue
      const predecessor = order[(position - 1 + order.length) % order.length]
      occurrences.push({
        periodIndex: entry.index,
        predecessorIndex: predecessor.index,
        startMs: occurrenceStart,
        endMs: clippedEnd,
      })
    }
  }
  return occurrences.sort((left, right) => left.startMs - right.startMs)
}

/**
 * Backend scheduled-segment parity at one instant: the containing occurrence's
 * value is its nominal target, ramped from the cyclic predecessor's value over
 * the configured ramp measured in absolute minutes from the resolved
 * (fold-first / gap-forward) start. A NULL target stays null, a missing
 * predecessor target steps to the nominal, a delta strictly below the shared
 * ramp-skip threshold steps (equality still ramps), and instants outside every
 * occurrence — including the window end — are null.
 */
function localScheduledValueAt(
  periods: readonly ClimatePeriod[],
  occurrences: readonly LocalOccurrence[],
  metric: ValueMetric,
  t: number
): number | null {
  let active: LocalOccurrence | null = null
  for (const occurrence of occurrences) {
    if (occurrence.startMs <= t && t < occurrence.endMs) active = occurrence
  }
  if (active === null) return null
  const period = periods[active.periodIndex]
  if (period === undefined) return null
  const target = period[METRIC_FIELD[metric]]
  if (target == null) return null
  const rampMs = Math.max(0, period.ramp_minutes) * 60_000
  const rampEnd = active.startMs + rampMs
  if (rampMs <= 0 || t >= rampEnd) return target
  const predecessor = periods[active.predecessorIndex]
  const initial = predecessor?.[METRIC_FIELD[metric]] ?? target
  if (Math.abs(initial - target) < rampSkipThreshold(metric)) return target
  return initial + (target - initial) * ((t - active.startMs) / rampMs)
}

/**
 * Transient drag-time rendering: evaluate the scheduled draft locally per
 * minute over the envelope window, matching the server's scheduled-segment
 * construction (resolved Toronto occurrence clocks, absolute ramp elapsed
 * minutes, cyclic same-profile predecessor, skip thresholds) rather than the
 * wall-minute table used by the periods table preview.
 */
export function buildLocalScheduledSeries(
  periods: readonly ClimatePeriod[],
  window: { readonly start: Date; readonly end: Date },
  sampleTimes: readonly number[] = envelopeMinuteGrid(window)
): LocalScheduledSeries {
  const occurrences = localScheduleOccurrences(
    periods,
    window.start.getTime(),
    window.end.getTime()
  )
  const series = new Map<ValueMetric, (number | null)[]>()
  for (const metric of LOCAL_METRICS) {
    series.set(
      metric,
      sampleTimes.map(t => localScheduledValueAt(periods, occurrences, metric, t))
    )
  }
  return { sampleTimes, series }
}

function envelopeMinuteGrid(window: { readonly start: Date; readonly end: Date }): number[] {
  const times: number[] = []
  for (let t = window.start.getTime(); t < window.end.getTime(); t += 60_000) times.push(t)
  times.push(window.end.getTime())
  return times
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
  schedule: (callback: () => void) => number = callback => window.requestAnimationFrame(callback),
  cancelScheduled: (id: number) => void = id => window.cancelAnimationFrame(id)
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
