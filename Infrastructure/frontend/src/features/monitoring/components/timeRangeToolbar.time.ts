/**
 * Pure time helpers for the monitoring time-range toolbar.
 *
 * Absolute inputs are Toronto wall time (`America/Toronto`). This module
 * resolves a wall-clock instant to a UTC `Date`, rejecting spring-forward
 * nonexistent times and requiring an explicit first-EDT / second-EST choice
 * for fall-back ambiguous times. It also owns preset definitions, range
 * validation, and URL serialization so the component stays a thin view.
 *
 * DST resolution uses a fixed-point search over candidate offsets: a wall
 * time is valid when exactly one UTC instant round-trips to it, ambiguous
 * (fall fold) when two do, and nonexistent (spring gap) when none do.
 */
import { formatInTimeZone, toZonedTime } from 'date-fns-tz'

export const TORONTO_TZ = 'America/Toronto'

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
  { label: 'All', duration: 7 * 24 * 3600_000 },
]

export interface WallComponents {
  y: number
  mo: number
  d: number
  h: number
  min: number
}

export type WallTimeResult =
  | { kind: 'valid'; utc: Date }
  | { kind: 'nonexistent' }
  | { kind: 'ambiguous'; firstUtc: Date; secondUtc: Date }

export type FallFoldChoice = 'first' | 'second'

/** Toronto UTC offset in milliseconds at a UTC instant. */
function offsetMsAt(utcMs: number): number {
  const str = formatInTimeZone(new Date(utcMs), TORONTO_TZ, 'XXX')
  const sign = str[0] === '-' ? -1 : 1
  const h = Number(str.slice(1, 3))
  const m = Number(str.slice(4, 6))
  return sign * (h * 3600 + m * 60) * 1000
}

/** Iterate `utc = wallAsUtc - offset(utc)`; null when it oscillates (gap). */
function fixedPoint(wallAsUtc: number, start: number): number | null {
  let utc = start
  const seen = new Set<number>()
  for (let i = 0; i < 8; i += 1) {
    if (seen.has(utc)) return null
    seen.add(utc)
    const next = wallAsUtc - offsetMsAt(utc)
    if (next === utc) return utc
    utc = next
  }
  return null
}

function matchesWall(back: Date, c: WallComponents): boolean {
  return (
    back.getFullYear() === c.y &&
    back.getMonth() === c.mo &&
    back.getDate() === c.d &&
    back.getHours() === c.h &&
    back.getMinutes() === c.min
  )
}

/** Parse a `YYYY-MM-DDTHH:mm` wall-time string into components. */
export function parseWallInput(input: string): WallComponents | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(input)
  if (m === null) return null
  const y = Number(m[1])
  const mo = Number(m[2]) - 1
  const d = Number(m[3])
  const h = Number(m[4])
  const min = Number(m[5])
  if (mo < 0 || mo > 11 || d < 1 || d > 31 || h > 23 || min > 59) return null
  return { y, mo, d, h, min }
}

/** Resolve a Toronto wall time to UTC, detecting DST gaps and folds. */
export function resolveWallTime(c: WallComponents): WallTimeResult {
  const wallAsUtc = Date.UTC(c.y, c.mo, c.d, c.h, c.min)
  const offsets = new Set<number>()
  for (let delta = -6; delta <= 6; delta += 1) {
    offsets.add(offsetMsAt(wallAsUtc + delta * 3600_000))
  }
  const candidates = new Set<number>()
  for (const offset of offsets) {
    const fp = fixedPoint(wallAsUtc, wallAsUtc - offset)
    if (fp === null) continue
    if (matchesWall(toZonedTime(new Date(fp), TORONTO_TZ), c)) candidates.add(fp)
  }
  const list = [...candidates].sort((a, b) => a - b)
  if (list.length === 0) return { kind: 'nonexistent' }
  if (list.length === 1) return { kind: 'valid', utc: new Date(list[0]) }
  return {
    kind: 'ambiguous',
    firstUtc: new Date(list[0]),
    secondUtc: new Date(list[1]),
  }
}

/** Resolve a wall time, applying an explicit fall-fold choice when ambiguous. */
export function resolveWallTimeWithChoice(
  c: WallComponents,
  choice: FallFoldChoice | null,
): WallTimeResult {
  const res = resolveWallTime(c)
  if (res.kind !== 'ambiguous' || choice === null) return res
  return { kind: 'valid', utc: choice === 'first' ? res.firstUtc : res.secondUtc }
}

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
  | { kind: 'live'; duration: number }
  | { kind: 'fixed'; start: Date; end: Date }

/** Serialize a range to a URL search-param string. */
export function serializeRange(range: ToolbarRange): string {
  if (range.kind === 'live') {
    const label = durationToLabel(range.duration)
    return label === null ? `range=live-${range.duration}` : `range=live-${label}`
  }
  return `start=${range.start.toISOString()}&end=${range.end.toISOString()}`
}

export type ParsedUrlRange =
  | { kind: 'live'; duration: number }
  | { kind: 'fixed'; start: Date; end: Date }
  | { kind: 'none' }

/** Parse URL search params into a range, or 'none' when absent/invalid. */
export function parseUrlRange(params: URLSearchParams): ParsedUrlRange {
  const rangeParam = params.get('range')
  if (rangeParam !== null) {
    const m = /^live-(.+)$/.exec(rangeParam)
    if (m !== null) {
      const label = m[1]
      const byLabel = PRESETS.find((p) => p.label === label)
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
