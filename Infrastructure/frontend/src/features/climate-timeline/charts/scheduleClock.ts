/**
 * Toronto schedule-clock conversion for the climate timeline.
 *
 * Stored period/photoperiod strings are `HH:MM` wall clocks in
 * `America/Toronto` (the same time base the automation service runs on), while
 * the chart renders an absolute UTC window. This module converts between the
 * two: Toronto date/clock extraction for sampling, exact wall-time resolution
 * for placing a stored clock on the axis, and occurrence expansion for
 * boundaries. DST folds resolve to the first occurrence and spring gaps shift
 * forward once (both decided by `resolveRecurringWallTime`); malformed input
 * or an unresolvable clock yields null — never a UTC fallback.
 */
import {
  parseWallInput,
  resolveRecurringWallTime,
  torontoDateString,
  torontoWallComponents,
} from '../../../utils/torontoWallTime'

const DAY_MS = 86_400_000

/** Toronto schedule-clock minutes-of-day (0–1439) of an instant. */
export function scheduleClockMinutes(instantMs: number): number {
  const components = torontoWallComponents(instantMs)
  return components.h * 60 + components.min
}

/**
 * Exact UTC instant of an `HH:MM` schedule clock on a Toronto local date
 * (`YYYY-MM-DD`). DST folds resolve to the first occurrence and gaps shift
 * forward once; malformed input or an unresolvable clock returns null.
 */
export function scheduleClockInstant(localDate: string, hhmm: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(localDate) || !/^\d{2}:\d{2}$/.test(hhmm)) return null
  const components = parseWallInput(`${localDate}T${hhmm}`)
  if (components === null) return null
  const resolved = resolveRecurringWallTime(components)
  return resolved.utc === null ? null : resolved.utc.getTime()
}

function localDateStartMs(localDate: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDate)
  if (m === null) return null
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

function localDateFromStartMs(startMs: number): string {
  const date = new Date(startMs)
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

/** The local date after `localDate` (`YYYY-MM-DD` → `YYYY-MM-DD`); null when malformed. */
export function scheduleNextLocalDate(localDate: string): string | null {
  const start = localDateStartMs(localDate)
  return start === null ? null : localDateFromStartMs(start + DAY_MS)
}

/**
 * Toronto local dates (`YYYY-MM-DD`) that can anchor occurrences intersecting
 * the half-open window: the local date preceding the window-start date (so a
 * night begun the previous Toronto day is found) through the local date
 * containing the last instant of the window.
 */
export function scheduleLocalDates(startMs: number, endMs: number): readonly string[] {
  if (endMs <= startMs) return []
  const firstStart = localDateStartMs(torontoDateString(startMs))
  const lastStart = localDateStartMs(torontoDateString(endMs - 1))
  if (firstStart === null || lastStart === null) return []

  const dates: string[] = []
  for (let dateStart = firstStart - DAY_MS; dateStart <= lastStart; dateStart += DAY_MS) {
    dates.push(localDateFromStartMs(dateStart))
  }
  return dates
}

/**
 * Every occurrence of an `HH:MM` schedule clock on the Toronto local dates
 * covering the half-open window `[startMs, endMs)`, ascending. A spring-gap
 * clock resolves to its forward-shifted occurrence; a clock that cannot
 * resolve at all is skipped.
 */
export function scheduleOccurrences(
  startMs: number,
  endMs: number,
  hhmm: string
): readonly number[] {
  if (endMs <= startMs) return []

  const occurrences: number[] = []
  for (const localDate of scheduleLocalDates(startMs, endMs)) {
    const instant = scheduleClockInstant(localDate, hhmm)
    if (instant !== null && instant >= startMs && instant < endMs) occurrences.push(instant)
  }
  return occurrences
}
