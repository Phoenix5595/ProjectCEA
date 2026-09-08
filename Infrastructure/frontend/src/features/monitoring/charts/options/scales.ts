/**
 * Builds the shared x time scale and per-family y scales/axes.
 *
 * Temperature is the only left-axis family; every other family renders on the
 * right. Scales are derived from the full series list (not visibility), so
 * hiding one series never removes its sibling family scale. Explicit canonical
 * bounds are preserved; families without one auto-range.
 */
import uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { resolveFamily, type ChartFamily } from './family'
import { familyColor } from './seriesOptions'

const RANGE_BOUNDS: Partial<
  Record<ChartFamily, {
    padEachSide?: number
    physicalMin?: number
    physicalMax?: number
  }>
> = {
  temperature: { padEachSide: 10 },
  rh: { padEachSide: 0.5, physicalMin: 0, physicalMax: 100 },
  vpd: { padEachSide: 0.5, physicalMin: 0 },
}

function paddedRange(
  minimumWindow?: number,
  maximumWindow?: number,
  minimumPadding?: number,
  physicalMinimum?: number,
  physicalMaximum?: number,
): (_self: uPlot, initMin: number | undefined, initMax: number | undefined) => [number, number] {
  return (_self, initMin, initMax) => {
    const observed = [initMin, initMax].filter(
      (value): value is number => typeof value === 'number' && Number.isFinite(value),
    )
    if (observed.length === 0) {
      const lo = minimumWindow ?? physicalMinimum ?? 0
      const hi = maximumWindow ?? physicalMaximum ?? lo + 1
      return lo < hi ? [lo, hi] : [lo, lo + 1]
    }
    const observedMin = Math.min(...observed)
    const observedMax = Math.max(...observed)
    const dataPadding = observedMin === observedMax
      ? Math.max(Math.abs(observedMin) * 0.05, 1)
      : (observedMax - observedMin) * 0.05
    const padding = Math.max(dataPadding, minimumPadding ?? 0)
    let lo = observedMin - padding
    let hi = observedMax + padding
    if (minimumWindow !== undefined && lo > minimumWindow) lo = minimumWindow
    if (maximumWindow !== undefined && hi < maximumWindow) hi = maximumWindow
    if (physicalMinimum !== undefined && lo < physicalMinimum) lo = physicalMinimum
    if (physicalMaximum !== undefined && hi > physicalMaximum) hi = physicalMaximum
    return [lo, hi]
  }
}

export interface ChartScales {
  scales: uPlot.Scales
  axes: uPlot.Axis[]
}

function readTokenXStroke(): string {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue('--text-muted')
    .trim()
  return value === '' ? 'rgba(200, 214, 194, 0.9)' : value
}

/** Build scales and axes for every family present in the data. */
export function buildScales(data: AlignedData): ChartScales {
  const families = new Set<ChartFamily>()
  for (const s of data.series) families.add(resolveFamily(s))

  const scales: uPlot.Scales = { x: { time: true } }
  const axes: uPlot.Axis[] = [
    {
      scale: 'x',
      stroke: readTokenXStroke(),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: 'rgba(128, 128, 128, 0.25)' },
    },
  ]

  for (const family of families) {
    // auto:true is required for uPlot to rescale user-defined value scales;
    // the bounded range function then clamps the proposed extent.
    const scale: uPlot.Scale = { auto: true }
    const bounds = RANGE_BOUNDS[family]
    scale.range = paddedRange(
      undefined,
      undefined,
      bounds?.padEachSide,
      bounds?.physicalMin,
      bounds?.physicalMax,
    )
    scales[family] = scale

    const isTemperature = family === 'temperature'
    axes.push({
      scale: family,
      side: isTemperature ? 3 : 1,
      stroke: familyColor(family),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: familyColor(family), size: 4 },
      gap: 2,
      font: '10px system-ui, sans-serif',
      size: 24,
    })
  }

  return { scales, axes }
}
