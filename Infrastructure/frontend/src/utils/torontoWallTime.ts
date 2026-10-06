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
