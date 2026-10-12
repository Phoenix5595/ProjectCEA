/**
 * Pure range helpers for the monitoring time-range toolbar.
 *
 * This module owns preset definitions, range validation, and URL serialization
 * so the component stays a thin view. Toronto wall-clock resolution lives in
 * `utils/torontoWallTime`.
 */
import { formatInTimeZone } from 'date-fns-tz'

import { TORONTO_TZ, subtractTorontoCalendarMonths } from '../../../utils/torontoWallTime'

export const MIN_RANGE_MS = 5 * 60 * 1000
export const MAX_RANGE_MS = 7 * 24 * 3600 * 1000

/**
 * Fixed-range cap with an opt-out for all-history pages.
 *
 * `undefined` keeps the detailed seven-day cap, `null` removes the fixed cap
 * (long fixed ranges opt into the overview surface), and a positive number is
 * an explicit cap. A `null` opt-out never applies to live windows: live ranges
 * always stay on the detailed seven-day maximum.
 */
export type MaxRangeCap = number | null

function capLabel(maxRangeMs: number): string {
  if (maxRangeMs === MAX_RANGE_MS) return '7 days'
  const days = maxRangeMs / (24 * 3600 * 1000)
  const daysText = Number.isInteger(days) ? `${days}` : days.toFixed(2)
  return `${daysText} day${days === 1 ? '' : 's'}`
}

export interface Preset {
  label: string
  duration: number
}

export const PRESETS: Preset[] = [
  { label: '1h', duration: 3600_000 },
  { label: '3h', duration: 3 * 3600_000 },
  { label: '6h', duration: 6 * 3600_000 },
  { label: '12h', duration: 12 * 3600_000 },
  { label: '24h', duration: 24 * 3600_000 },
  { label: '7d', duration: 7 * 24 * 3600_000 },
]

/** Toronto offset label for a UTC instant, e.g. "EDT UTC-04:00". */
export function offsetLabel(utc: Date): string {
  return `${formatInTimeZone(utc, TORONTO_TZ, 'zzz')} UTC${formatInTimeZone(utc, TORONTO_TZ, 'XXX')}`
}

/**
 * Validate a fixed range is within 5 minutes and the requested cap; returns an
 * error message or null. `maxRangeMs` follows `MaxRangeCap`: the default caps
 * fixed ranges at seven days and `null` removes the fixed cap for opt-in
 * all-history pages. Non-finite or reversed bounds are always rejected.
 */
export function validateRange(
  start: Date,
  end: Date,
  maxRangeMs: MaxRangeCap = MAX_RANGE_MS
): string | null {
  const startMs = start.getTime()
  const endMs = end.getTime()
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    return 'Range bounds must be valid dates'
  }
  const dur = endMs - startMs
  if (dur < MIN_RANGE_MS) return 'Range must be at least 5 minutes'
  if (maxRangeMs !== null && dur > maxRangeMs) {
    return `Range must not exceed ${capLabel(maxRangeMs)}`
  }
  return null
}

/** Map a duration to its preset label, or null when not a preset. */
export function durationToLabel(duration: number): string | null {
  for (const p of PRESETS) if (p.duration === duration) return p.label
  return null
}

/**
 * Minimum history span on the Historical routes, in milliseconds. A fixed
 * range shorter than this is the regular Monitoring surface, never history:
 * the pages route it to `/{sector}/monitoring` with the exact range instead of
 * stretching it to the minimum.
 */
export const MIN_HISTORY_MS = 5 * 24 * 3600_000

/**
 * Historical-route preset: a fixed `now`-anchored range, never a live window.
 *
 * `duration` presets subtract a fixed millisecond span from `now`; `months`
 * presets subtract whole Toronto calendar months (day clamp + DST resolution).
 * `route` names the surface the preset lands on: `monitoring` for the
 * five-day minimum-history handoff, `history` for the Historical page itself.
 */
export interface HistoricalPreset {
  readonly label: string
  readonly route: 'history' | 'monitoring'
  readonly duration?: number
  readonly months?: number
}

/** Historical-route replacement for the live `PRESETS` group. */
export const HISTORICAL_PRESETS: readonly HistoricalPreset[] = [
  { label: '5 days', route: 'monitoring', duration: MIN_HISTORY_MS },
  { label: '7 days', route: 'history', duration: 7 * 24 * 3600_000 },
  { label: '2 weeks', route: 'history', duration: 14 * 24 * 3600_000 },
  { label: '1 month', route: 'history', months: 1 },
  { label: '3 months', route: 'history', months: 3 },
]

/**
 * `end`-anchored fixed range for a historical preset. Duration presets
 * subtract milliseconds; month presets subtract whole Toronto calendar months
 * with day clamp and DST resolution. An unresolvable target returns null —
 * never a fallback range.
 */
export function historicalPresetRange(
  preset: HistoricalPreset,
  end: Date
): { start: Date; end: Date } | null {
  if (preset.months !== undefined) {
    const start = subtractTorontoCalendarMonths(end, preset.months)
    return start === null ? null : { start, end }
  }
  if (preset.duration !== undefined) {
    return { start: new Date(end.getTime() - preset.duration), end }
  }
  return null
}

export type ToolbarRange =
  { kind: 'live'; duration: number } | { kind: 'fixed'; start: Date; end: Date }

/** Serialize a range to a URL search-param string. */
export function serializeRange(range: ToolbarRange): string {
  if (range.kind === 'live') {
    const label = durationToLabel(range.duration)
    return label === null ? `range=live-${range.duration}` : `range=live-${label}`
  }
  return `start=${range.start.toISOString()}&end=${range.end.toISOString()}`
}

/**
 * Return `params` with the range replaced by `range`'s serialization. The three
 * dedicated range keys are removed first so preset/numeric-live and absolute
 * serialized forms never mix.
 */
export function withSerializedRange(
  params: URLSearchParams,
  range: ToolbarRange
): URLSearchParams {
  const next = new URLSearchParams(params)
  next.delete('range')
  next.delete('start')
  next.delete('end')
  new URLSearchParams(serializeRange(range)).forEach((value, key) => next.set(key, value))
  return next
}

export type ParsedUrlRange =
  { kind: 'live'; duration: number } | { kind: 'fixed'; start: Date; end: Date } | { kind: 'none' }

/**
 * Parse URL search params into a range, or 'none' when absent/invalid.
 *
 * `maxRangeMs` follows `MaxRangeCap`: the default parses only fixed ranges of
 * at most seven days and `null` accepts unlimited fixed ranges for opt-in
 * all-history pages. Numeric live windows always stay on the detailed
 * seven-day maximum regardless of the cap, because live ranges are detailed
 * surfaces only.
 */
export function parseUrlRange(
  params: URLSearchParams,
  maxRangeMs: MaxRangeCap = MAX_RANGE_MS
): ParsedUrlRange {
  const rangeParam = params.get('range')
  if (rangeParam !== null) {
    const m = /^live-(.+)$/.exec(rangeParam)
    if (m !== null) {
      const label = m[1]
      const byLabel = PRESETS.find(p => p.label === label)
      if (byLabel !== undefined) return { kind: 'live', duration: byLabel.duration }
      const ms = Number(label)
      if (Number.isFinite(ms) && ms > 0 && ms <= MAX_RANGE_MS) {
        return { kind: 'live', duration: ms }
      }
    }
    return { kind: 'none' }
  }
  const startStr = params.get('start')
  const endStr = params.get('end')
  if (startStr === null || endStr === null) return { kind: 'none' }
  const start = new Date(startStr)
  const end = new Date(endStr)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { kind: 'none' }
  if (validateRange(start, end, maxRangeMs) !== null) return { kind: 'none' }
  return { kind: 'fixed', start, end }
}
