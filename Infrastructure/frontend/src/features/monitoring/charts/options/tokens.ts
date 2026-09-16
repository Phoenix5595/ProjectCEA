/**
 * Runtime access to the monitoring design tokens.
 *
 * Reads CSS custom properties from the document root via `getComputedStyle`.
 * jsdom (tests) does not apply stylesheets, so every token carries a
 * deterministic fallback so the chart still renders and tests stay stable.
 * Production always resolves the real theme value from `src/styles/themes.css`.
 */
export const MONITORING_TOKEN_NAMES = {
  familyTemperature: '--mon-family-temperature',
  familyRh: '--mon-family-rh',
  familyVpd: '--mon-family-vpd',
  familyCo2: '--mon-family-co2',
  familyPressure: '--mon-family-pressure',
  familyDevice: '--mon-family-device',
  familyLight: '--mon-family-light',
  nodeFront: '--mon-node-front',
  nodeBack: '--mon-node-back',
  envelopeFill: '--mon-envelope-fill',
  envelopeStroke: '--mon-envelope-stroke',
  targetRecorded: '--mon-target-recorded',
  targetProjected: '--mon-target-projected',
  targetProjectedOpacity: '--mon-target-projected-opacity',
  targetDash: '--mon-target-dash',
  sunBg: '--mon-sun-bg',
  moonBg: '--mon-moon-bg',
  focusRing: '--mon-focus-ring',
  tooltipBg: '--mon-tooltip-bg',
  tooltipBorder: '--mon-tooltip-border',
  tooltipText: '--mon-tooltip-text',
  stale: '--mon-stale',
  error: '--mon-error',
  axisLeft: '--mon-axis-left',
  axisRight: '--mon-axis-right',
} as const

export type MonitoringTokenName = keyof typeof MONITORING_TOKEN_NAMES

/** Deterministic fallbacks mirroring the default (botanical) theme. */
const FALLBACK: Record<MonitoringTokenName, string> = {
  familyTemperature: '#bd4253',
  familyRh: '#00fff2',
  familyVpd: '#c0dd55',
  familyCo2: '#b18981',
  familyPressure: '#769867',
  familyDevice: '#c8d6c2',
  familyLight: '#c0dd55',
  nodeFront: '#00fff2',
  nodeBack: '#c0dd55',
  envelopeFill: 'rgba(0, 255, 242, 0.14)',
  envelopeStroke: 'rgba(0, 255, 242, 0.45)',
  targetRecorded: '#c0dd55',
  targetProjected: '#c0dd55',
  targetProjectedOpacity: '0.5',
  targetDash: '4 4',
  sunBg: 'rgba(192, 221, 85, 0.08)',
  moonBg: 'rgba(0, 153, 145, 0.08)',
  focusRing: '#00fff2',
  tooltipBg: '#181e15',
  tooltipBorder: '#5e7953',
  tooltipText: '#f1f5f0',
  stale: '#b18981',
  error: '#bd4253',
  axisLeft: '#bd4253',
  axisRight: '#00fff2',
}

/** Resolve a monitoring token, falling back when the theme is not applied. */
export function readToken(name: MonitoringTokenName): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(MONITORING_TOKEN_NAMES[name])
    .trim()
  return value === '' ? FALLBACK[name] : value
}
