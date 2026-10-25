/**
 * Pure Toronto wall-clock owner shared across features.
 *
 * Absolute inputs are Toronto wall time (`America/Toronto`). This module
 * resolves a wall-clock instant to a UTC `Date`, rejecting spring-forward
 * nonexistent times and resolving fall-back ambiguous times (explicit
 * first-EDT / second-EST choice for the absolute toolbar; first occurrence for
 * recurring schedule clocks).
 *
 * DST resolution uses a fixed-point search over candidate offsets: a wall
 * time is valid when exactly one UTC instant round-trips to it, ambiguous
 * (fall fold) when two do, and nonexistent (spring gap) when none do.
 *
 * The historical-range helpers build on that core:
 * `subtractTorontoCalendarMonths` (whole-month subtraction with day clamp and
 * deterministic DST), `torontoMidnightUtc` (Toronto midnight of a calendar
 * date) and `torontoDayWindow` (the Toronto local calendar day containing an
 * instant). The display formatters (`formatTorontoClock`,
 * `formatTorontoClockSeconds`) render chart axis ticks and tooltip timestamps
 * in the same wall clock.
 */
import { formatInTimeZone, toZonedTime } from 'date-fns-tz'

export const TORONTO_TZ = 'America/Toronto'

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

/** How a recurring wall time was resolved across a DST transition. */
export type DstAssumption = 'fold-first' | 'gap-forward'

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
  choice: FallFoldChoice | null
): WallTimeResult {
  const res = resolveWallTime(c)
  if (res.kind !== 'ambiguous' || choice === null) return res
  return { kind: 'valid', utc: choice === 'first' ? res.firstUtc : res.secondUtc }
}

/**
 * Resolve a wall time that recurs on a schedule, picking a deterministic UTC
 * instant across DST transitions: a valid time resolves to the same UTC; a
 * fall fold resolves to the first (EDT) occurrence; a spring gap shifts the
 * requested wall clock forward by the observed offset jump and resolves once;
 * anything unresolved yields null — never a UTC fallback.
 */
export function resolveRecurringWallTime(c: WallComponents): {
  utc: Date | null
  dstAssumption: DstAssumption | null
} {
  const resolved = resolveWallTime(c)
  if (resolved.kind === 'valid') return { utc: resolved.utc, dstAssumption: null }
  if (resolved.kind === 'ambiguous') {
    return { utc: resolved.firstUtc, dstAssumption: 'fold-first' }
  }
  const wallAsUtc = Date.UTC(c.y, c.mo, c.d, c.h, c.min)
  const delta = offsetMsAt(wallAsUtc + 6 * 3600_000) - offsetMsAt(wallAsUtc - 6 * 3600_000)
  if (delta <= 0) return { utc: null, dstAssumption: null }
  const shifted = new Date(wallAsUtc + delta)
  const shiftedComponents: WallComponents = {
    y: shifted.getUTCFullYear(),
    mo: shifted.getUTCMonth(),
    d: shifted.getUTCDate(),
    h: shifted.getUTCHours(),
    min: shifted.getUTCMinutes(),
  }
  const once = resolveWallTime(shiftedComponents)
  if (once.kind !== 'valid') return { utc: null, dstAssumption: null }
  return { utc: once.utc, dstAssumption: 'gap-forward' }
}

/** Toronto wall components (local date and clock) of a UTC instant. */
export function torontoWallComponents(instantMs: number): WallComponents {
  const zoned = toZonedTime(new Date(instantMs), TORONTO_TZ)
  return {
    y: zoned.getFullYear(),
    mo: zoned.getMonth(),
    d: zoned.getDate(),
    h: zoned.getHours(),
    min: zoned.getMinutes(),
  }
}

/** `YYYY-MM-DD` of the Toronto local date containing the instant. */
export function torontoDateString(instantMs: number): string {
  const c = torontoWallComponents(instantMs)
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${c.y}-${pad(c.mo + 1)}-${pad(c.d)}`
}

/**
 * Subtract whole Toronto calendar months from a UTC instant, keeping the
 * Toronto wall clock (never a fixed millisecond count), clamping the day of
 * month to the target month's length, and resolving DST through
 * `resolveRecurringWallTime` (fall fold → first EDT occurrence; spring gap →
 * shifted forward by the offset jump). Returns null when the target wall time
 * cannot be resolved — never a UTC fallback.
 */
export function subtractTorontoCalendarMonths(end: Date, months: number): Date | null {
  if (!(end instanceof Date) || Number.isNaN(end.getTime())) return null
  if (!Number.isInteger(months)) return null
  const wall = torontoWallComponents(end.getTime())
  const total = wall.y * 12 + wall.mo - months
  const y = Math.floor(total / 12)
  const mo = ((total % 12) + 12) % 12
  const dayLimit = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate()
  const resolved = resolveRecurringWallTime({
    y,
    mo,
    d: Math.min(wall.d, dayLimit),
    h: wall.h,
    min: wall.min,
  })
  if (resolved.utc !== null) {
    resolved.utc.setUTCSeconds(end.getUTCSeconds(), end.getUTCMilliseconds())
  }
  return resolved.utc
}

/**
 * UTC instant of Toronto-local midnight for a `YYYY-MM-DD` calendar date, or
 * null when the string or date does not exist — never an invented offset.
 */
export function torontoMidnightUtc(dateStr: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr)
  if (m === null) return null
  const resolved = resolveWallTime({
    y: Number(m[1]),
    mo: Number(m[2]) - 1,
    d: Number(m[3]),
    h: 0,
    min: 0,
  })
  return resolved.kind === 'valid' ? resolved.utc : null
}

/**
 * Toronto local calendar day containing a UTC instant: Toronto midnight of
 * that local date through Toronto midnight of the next local date. The span is
 * 23, 24 or 25 hours on DST change dates — never a fixed millisecond count.
 */
export function torontoDayWindow(instantMs: number): { start: number; end: number } {
  const start = torontoMidnightUtc(torontoDateString(instantMs))
  if (start === null) throw new Error('Toronto local midnight did not resolve')
  const end = torontoMidnightUtc(nextTorontoDateString(start.getTime()))
  if (end === null) throw new Error('Toronto local midnight did not resolve')
  return { start: start.getTime(), end: end.getTime() }
}

/** `YYYY-MM-DD` of the Toronto local date after the one containing the instant. */
function nextTorontoDateString(instantMs: number): string {
  const c = torontoWallComponents(instantMs)
  const next = new Date(Date.UTC(c.y, c.mo, c.d + 1))
  const month = String(next.getUTCMonth() + 1).padStart(2, '0')
  const day = String(next.getUTCDate()).padStart(2, '0')
  return `${next.getUTCFullYear()}-${month}-${day}`
}

const CLOCK_FORMAT = new Intl.DateTimeFormat('en-CA', {
  timeZone: TORONTO_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const CLOCK_SECONDS_FORMAT = new Intl.DateTimeFormat('en-CA', {
  timeZone: TORONTO_TZ,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
  hourCycle: 'h23',
})

/** `HH:MM` Toronto wall clock of a UTC instant. */
export function formatTorontoClock(instantMs: number): string {
  return CLOCK_FORMAT.format(new Date(instantMs))
}

/** `HH:MM:SS.mmm` Toronto wall clock of a UTC instant. */
export function formatTorontoClockSeconds(instantMs: number): string {
  return CLOCK_SECONDS_FORMAT.format(new Date(instantMs))
}
