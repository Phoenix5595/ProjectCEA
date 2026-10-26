/** Shared soil formatting helpers. */

/** `YYYY-MM-DD HH:MM:SS UTC` for an instant. */
export function formatUtcInstant(date: Date): string {
  return `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

const HOUR_MS = 3_600_000

/** Age of a past instant: whole hours below 48 h, else whole days (floored). */
export function formatPastAge(ms: number): string {
  const hours = Math.floor(Math.max(0, ms) / HOUR_MS)
  return hours < 48 ? `${hours} h` : `${Math.floor(hours / 24)} d`
}
