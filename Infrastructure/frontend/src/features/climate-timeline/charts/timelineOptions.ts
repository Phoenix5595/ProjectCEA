import uPlot from 'uplot'

import type { EnvelopeSeriesKey } from './envelopeSeries'
import { readTimelineToken, type TimelineTokenName } from './tokens'

export type TimelineScale = 'temp' | 'vpd' | 'co2'

/**
 * Chart authority roles. Series keys are role-prefixed so the same metric can
 * appear under different authorities at once:
 *
 * - `active-current` — fresh observed fact from the completed tick (points).
 * - `active-future` — canonical running forecast, estimated (dashed).
 * - `selected-saved` — hypothetical saved schedule of the inspected profile.
 * - `selected-draft` — reviewed or local-estimate draft of the inspected
 *   profile; the only editable role.
 */
export type TimelineSeriesRole = 'active-current' | 'active-future' | 'selected-saved' | 'selected-draft'

/** Role-prefixed series key: `${TimelineSeriesRole}:${EnvelopeSeriesKey}`. */
export type TimelineSeriesKey = `${TimelineSeriesRole}:${EnvelopeSeriesKey}`

export interface TimelineSeriesMeta {
  readonly key: TimelineSeriesKey
  readonly label: string
  readonly metric: string
  readonly scale: TimelineScale
  readonly stroke: string
  readonly dash: readonly number[]
  /** Chart authority role of this series. */
  readonly role: TimelineSeriesRole
  /** Rich trajectory kind; authority is independently encoded by role. */
  readonly kind: 'scheduled' | 'effective'
}

export const EFFECTIVE_DASH = [6, 4] as const
export const CURRENT_MARKER_DIAMETER_PX = 6
/** Selected-draft curve: dotted so it never reads as an actual fact. */
export const DRAFT_DASH = [2, 3] as const

const METRIC_LABELS: Record<string, string> = {
  heating_setpoint: 'Heating',
  cooling_setpoint: 'Cooling',
  vpd_setpoint: 'VPD',
  co2_setpoint: 'CO₂',
}

const METRIC_TOKENS: Record<string, TimelineTokenName> = {
  heating_setpoint: 'heating',
  cooling_setpoint: 'cooling',
  vpd_setpoint: 'vpd',
  co2_setpoint: 'co2',
}

const SCALE_TOKENS: Record<TimelineScale, TimelineTokenName> = {
  temp: 'heating',
  vpd: 'vpd',
  co2: 'co2',
}

export const UNIT_LABELS: Record<TimelineScale, string> = {
  temp: '°C',
  vpd: 'kPa',
  co2: 'ppm',
}

/** Product validation ranges act as the soft bounds of each family scale. */
const SOFT_BOUNDS: Record<TimelineScale, { readonly min: number; readonly max: number }> = {
  temp: { min: 10, max: 35 },
  vpd: { min: 0, max: 5 },
  co2: { min: 400, max: 2000 },
}

export function timelineScaleForMetric(metric: string): TimelineScale {
  if (metric === 'co2_setpoint') return 'co2'
  if (metric === 'vpd_setpoint') return 'vpd'
  return 'temp'
}

export function timelineSeriesMeta(
  keys: readonly EnvelopeSeriesKey[],
  role: TimelineSeriesRole,
  labelPrefix: string
): TimelineSeriesMeta[] {
  return keys.map(key => {
    const separator = key.lastIndexOf(':')
    const metric = key.slice(0, separator)
    const kind = key.slice(separator + 1) as TimelineSeriesMeta['kind']
    return {
      key: `${role}:${key}` as TimelineSeriesKey,
      metric,
      label: `${labelPrefix}${METRIC_LABELS[metric] ?? metric} (${kind})`,
      scale: timelineScaleForMetric(metric),
      stroke: readTimelineToken(METRIC_TOKENS[metric] ?? 'heating'),
      dash: role === 'active-future' ? EFFECTIVE_DASH : role === 'selected-draft' ? DRAFT_DASH : [],
      role,
      kind,
    }
  })
}

/** Soft-bounded family range: follows the data with padding, clamped to the soft window. */
export function softRange(
  softMin: number,
  softMax: number
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    const hasMin = typeof initMin === 'number' && Number.isFinite(initMin)
    const hasMax = typeof initMax === 'number' && Number.isFinite(initMax)
    let lo = hasMin ? initMin : softMin
    let hi = hasMax ? initMax : softMax
    if (hasMin && hasMax) {
      const padding =
        initMin === initMax ? Math.max(Math.abs(initMin) * 0.05, 1) : (initMax - initMin) * 0.05
      lo = Math.min(lo, initMin - padding)
      hi = Math.max(hi, initMax + padding)
    }
    if (lo > softMin) lo = softMin
    if (hi < softMax) hi = softMax
    if (lo >= hi) hi = lo + 1
    return [lo, hi]
  }
}

export interface TimelineWindowMs {
  readonly start: number
  readonly end: number
}

export interface TimelineHooks {
  readonly onSetScale?: (self: uPlot, scaleKey: string) => void
}

export function buildTimelineOptions(
  meta: readonly TimelineSeriesMeta[],
  width: number,
  height: number,
  windowMs: TimelineWindowMs,
  hooks: TimelineHooks,
  plugins: uPlot.Plugin[]
): uPlot.Options {
  const scales: uPlot.Scales = {
    x: { time: false, range: () => [0, 1441] },
    temp: { auto: true, range: softRange(SOFT_BOUNDS.temp.min, SOFT_BOUNDS.temp.max) },
    vpd: { auto: true, range: softRange(SOFT_BOUNDS.vpd.min, SOFT_BOUNDS.vpd.max) },
    co2: { auto: true, range: softRange(SOFT_BOUNDS.co2.min, SOFT_BOUNDS.co2.max) },
  }

  const axisStroke = readTimelineToken('axis')
  const gridStroke = readTimelineToken('grid')
  const axes: uPlot.Axis[] = [
    {
      scale: 'x',
      stroke: axisStroke,
      grid: { stroke: gridStroke },
      ticks: { stroke: gridStroke },
      size: 30,
      values: (_self, splits) =>
        splits.map(minutes => {
          const instant = new Date(windowMs.start + minutes * 60_000)
          const hours = String(instant.getUTCHours()).padStart(2, '0')
          const minutesText = String(instant.getUTCMinutes()).padStart(2, '0')
          return `${hours}:${minutesText}`
        }),
    },
    {
      scale: 'temp',
      side: 3,
      stroke: readTimelineToken(SCALE_TOKENS.temp),
      grid: { stroke: gridStroke },
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.temp) },
      size: 25,
    },
    {
      scale: 'vpd',
      side: 1,
      stroke: readTimelineToken(SCALE_TOKENS.vpd),
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.vpd) },
      size: 25,
    },
    {
      scale: 'co2',
      side: 1,
      stroke: readTimelineToken(SCALE_TOKENS.co2),
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.co2) },
      size: 25,
      values: (_self, splits) =>
        splits.map(value => (value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value))),
    },
  ]

  const series: uPlot.Series[] = [
    {},
    ...meta.map(entry => ({
      label: entry.label,
      stroke: entry.stroke,
      width: entry.role === 'active-current' ? 0 : 1,
      dash: [...entry.dash],
      scale: entry.scale,
      spanGaps: false,
      points: { show: entry.role === 'active-current', size: CURRENT_MARKER_DIAMETER_PX },
    })),
  ]

  const cursorPosition: [number, number] = [0, 0]
  return {
    width,
    height,
    scales,
    axes,
    series,
    plugins,
    legend: { show: false },
    cursor: {
      points: { show: false },
      move: (self, left, top) => {
        // The existing desktop zoom changes screen pixels, not canvas coordinates.
        const rect = self.rect
        cursorPosition[0] = left * self.bbox.width / (rect.width * uPlot.pxRatio)
        cursorPosition[1] = top * self.bbox.height / (rect.height * uPlot.pxRatio)
        return cursorPosition
      },
    },
    hooks: (() => {
      const assembled: NonNullable<uPlot.Options['hooks']> = {}
      if (hooks.onSetScale) {
        assembled.setScale = [(self: uPlot, scaleKey: string) => hooks.onSetScale?.(self, scaleKey)]
      }
      return assembled
    })(),
  }
}
