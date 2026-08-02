/**
 * Pure formatting helpers for monitoring tables.
 *
 * Values are formatted with the canonical decimal count for their unit family
 * (celsius/percent/hpa/mm = 1, kpa = 2, ppm = 0), overridable by an explicit
 * `decimals` value. Timestamps render as compact local wall time matching the
 * canonical `YYYY/MM/DD HH24:MI:SS` Last Update format. Staleness is decided by
 * comparing a live observation timestamp against `now`.
 */

/** Canonical decimal count per unit family. */
export const FAMILY_DECIMALS: Record<string, number> = {
  celsius: 1,
  percent: 1,
  kpa: 2,
  ppm: 0,
  hpa: 1,
  mm: 1,
}

/** Display unit string per unit family. */
export const FAMILY_TO_UNIT: Record<string, string> = {
  celsius: '°C',
  percent: '%',
  kpa: ' kPa',
  ppm: ' ppm',
  hpa: ' hPa',
  mm: ' mm',
}

/** Default staleness threshold for live values (60 s). */
export const DEFAULT_STALE_AFTER_MS = 60_000

/** Format a numeric value with the family's canonical (or explicit) decimals. */
export function formatValue(value: number, family: string, decimals?: number): string {
  const d = decimals ?? FAMILY_DECIMALS[family] ?? 1
  return value.toFixed(d)
}

/** True when a live observation is older than the staleness threshold. */
export function isStale(
  timestamp: Date,
  now: Date,
  staleAfterMs: number = DEFAULT_STALE_AFTER_MS,
): boolean {
  return now.getTime() - timestamp.getTime() > staleAfterMs
}

/** Render a UTC Date as a compact local wall-time string. */
export function formatTimestamp(timestamp: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${timestamp.getFullYear()}/${pad(timestamp.getMonth() + 1)}/${pad(timestamp.getDate())} ` +
    `${pad(timestamp.getHours())}:${pad(timestamp.getMinutes())}:${pad(timestamp.getSeconds())}`
  )
}
