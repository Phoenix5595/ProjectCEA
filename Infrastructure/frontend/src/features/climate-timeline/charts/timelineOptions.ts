import type uPlot from 'uplot'
import type { EnvelopeSeriesKey } from './envelopeSeries'
import { readTimelineToken, type TimelineTokenName } from './tokens'

export type TimelineScale = 'temp' | 'vpd' | 'co2'

export interface TimelineSeriesMeta {
  readonly key: EnvelopeSeriesKey
  readonly label: string
  readonly metric: string
  readonly scale: TimelineScale
  readonly stroke: string
  readonly dash: readonly number[]
}

export const EFFECTIVE_DASH = [6, 4] as const

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

const UNIT_LABELS: Record<TimelineScale, string> = {
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

export function timelineSeriesMeta(keys: readonly EnvelopeSeriesKey[]): TimelineSeriesMeta[] {
  return keys.map((key) => {
    const separator = key.lastIndexOf(':')
    const metric = key.slice(0, separator)
    const kind = key.slice(separator + 1)
    return {
      key,
      metric,
      label: `${METRIC_LABELS[metric] ?? metric} (${kind})`,
      scale: timelineScaleForMetric(metric),
      stroke: readTimelineToken(METRIC_TOKENS[metric] ?? 'heating'),
      dash: kind === 'effective' ? EFFECTIVE_DASH : [],
    }
  })
}

/** Soft-bounded family range: follows the data with padding, clamped to the soft window. */
export function softRange(
  softMin: number,
  softMax: number,
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    const hasMin = typeof initMin === 'number' && Number.isFinite(initMin)
    const hasMax = typeof initMax === 'number' && Number.isFinite(initMax)
    let lo = hasMin ? initMin : softMin
    let hi = hasMax ? initMax : softMax
    if (hasMin && hasMax) {
      const padding = initMin === initMax
        ? Math.max(Math.abs(initMin) * 0.05, 1)
        : (initMax - initMin) * 0.05
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
  plugins: uPlot.Plugin[],
): uPlot.Options {
  const scales: uPlot.Scales = {
    x: { time: true, range: () => [windowMs.start, windowMs.end] },
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
    },
    {
      scale: 'temp',
      side: 3,
      stroke: readTimelineToken(SCALE_TOKENS.temp),
      grid: { stroke: gridStroke },
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.temp) },
      size: 44,
      label: UNIT_LABELS.temp,
      labelSize: 10,
    },
    {
      scale: 'vpd',
      side: 1,
      stroke: readTimelineToken(SCALE_TOKENS.vpd),
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.vpd) },
      size: 44,
      label: UNIT_LABELS.vpd,
      labelSize: 10,
    },
    {
      scale: 'co2',
      side: 1,
      stroke: readTimelineToken(SCALE_TOKENS.co2),
      ticks: { stroke: readTimelineToken(SCALE_TOKENS.co2) },
      size: 44,
      label: UNIT_LABELS.co2,
      labelSize: 10,
    },
  ]

  const series: uPlot.Series[] = [
    {},
    ...meta.map((entry) => ({
      label: entry.label,
      stroke: entry.stroke,
      width: 1,
      dash: [...entry.dash],
      scale: entry.scale,
      spanGaps: false,
      points: { show: false },
    })),
  ]

  return {
    width,
    height,
    scales,
    axes,
    series,
    plugins,
    legend: { show: false },
    cursor: { points: { show: false } },
    hooks: {
      setScale: hooks.onSetScale
        ? [(self: uPlot, scaleKey: string) => hooks.onSetScale?.(self, scaleKey)]
        : undefined,
    },
  }
}
