/**
 * Time conversion utilities for climatePeriodTimeline.
 */

/**
 * Convert time string (HH:MM) to minutes since midnight.
 * Handles both simple HH:MM and trimmed input.
 */
export function timeToMinutes(time: string): number {
  const parts = time.trim().split(':')
  const h = Number(parts[0])
  const m = Number(parts[1] ?? 0)
  if (Number.isNaN(h) || Number.isNaN(m)) return 0
  return (h * 60 + m) % 1440
}

/**
 * Convert minutes since midnight to time string (HH:MM).
 */
export function minutesToTime(minutes: number): string {
  const hours = Math.floor(minutes / 60) % 24
  const mins = minutes % 60
  return `${hours.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}`
}
