/**
 * Pure range helpers for the monitoring time-range toolbar.
 *
 * This module owns preset definitions, range validation, and URL serialization
 * so the component stays a thin view. Toronto wall-clock resolution lives in
 * `utils/torontoWallTime`.
 */
import { formatInTimeZone } from 'date-fns-tz'

import { TORONTO_TZ } from '../../../utils/torontoWallTime'

export const MIN_RANGE_MS = 5 * 60 * 1000
export const MAX_RANGE_MS = 7 * 24 * 3600 * 1000

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

/** Validate a fixed range is within 5m–7d; returns an error message or null. */
export function validateRange(start: Date, end: Date): string | null {
  const dur = end.getTime() - start.getTime()
  if (dur < MIN_RANGE_MS) return 'Range must be at least 5 minutes'
  if (dur > MAX_RANGE_MS) return 'Range must not exceed 7 days'
  return null
}

/** Map a duration to its preset label, or null when not a preset. */
export function durationToLabel(duration: number): string | null {
  for (const p of PRESETS) if (p.duration === duration) return p.label
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

export type ParsedUrlRange =
  { kind: 'live'; duration: number } | { kind: 'fixed'; start: Date; end: Date } | { kind: 'none' }

/** Parse URL search params into a range, or 'none' when absent/invalid. */
export function parseUrlRange(params: URLSearchParams): ParsedUrlRange {
  const rangeParam = params.get('range')
  if (rangeParam !== null) {
    const m = /^live-(.+)$/.exec(rangeParam)
    if (m !== null) {
      const label = m[1]
      const byLabel = PRESETS.find(p => p.label === label)
      if (byLabel !== undefined) return { kind: 'live', duration: byLabel.duration }
      const ms = Number(label)
      if (Number.isFinite(ms) && ms > 0) return { kind: 'live', duration: ms }
    }
    return { kind: 'none' }
  }
  const startStr = params.get('start')
  const endStr = params.get('end')
  if (startStr === null || endStr === null) return { kind: 'none' }
  const start = new Date(startStr)
  const end = new Date(endStr)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { kind: 'none' }
  if (validateRange(start, end) !== null) return { kind: 'none' }
  return { kind: 'fixed', start, end }
}
