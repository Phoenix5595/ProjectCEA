export const TIMELINE_TOKEN_NAMES = {
  heating: '--tl-heating',
  cooling: '--tl-cooling',
  vpd: '--tl-vpd',
  co2: '--tl-co2',
  axis: '--tl-axis',
  grid: '--tl-grid',
  now: '--tl-now',
} as const

export type TimelineTokenName = keyof typeof TIMELINE_TOKEN_NAMES

const FALLBACK: Record<TimelineTokenName, string> = {
  heating: '#ea580c',
  cooling: '#3b82f6',
  vpd: '#22c55e',
  co2: '#64748b',
  axis: 'rgba(128, 128, 128, 0.9)',
  grid: 'rgba(128, 128, 128, 0.15)',
  now: '#e11d48',
}

export function readTimelineToken(name: TimelineTokenName): string {
  if (typeof document === 'undefined') return FALLBACK[name]
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(TIMELINE_TOKEN_NAMES[name])
    .trim()
  return value === '' ? FALLBACK[name] : value
}
