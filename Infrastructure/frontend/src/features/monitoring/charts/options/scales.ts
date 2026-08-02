/**
 * Builds the shared x time scale and per-family y scales/axes.
 *
 * Temperature is the only left-axis family; every other family renders on the
 * right. Scales are derived from the full series list (not visibility), so
 * hiding one series never removes its sibling family scale. Explicit canonical
 * soft ranges are preserved; families without one auto-range.
 */
import uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { resolveFamily, type ChartFamily } from './family'
import { familyColor } from './seriesOptions'

/** Canonical soft bounds per family (see DESIGN.md section 2). */
const SOFT_RANGES: Partial<
  Record<ChartFamily, { min?: { soft: number }; max?: { soft: number } }>
> = {
  temperature: { min: { soft: 15 } },
  rh: { max: { soft: 100 } },
  device: { min: { soft: 0 }, max: { soft: 100 } },
  light: { min: { soft: 0 }, max: { soft: 100 } },
  pressure: { min: { soft: 1012 }, max: { soft: 1014 } },
}

export interface ChartScales {
  scales: uPlot.Scales
  axes: uPlot.Axis[]
}

/** Build scales and axes for every family present in the data. */
export function buildScales(data: AlignedData): ChartScales {
  const families = new Set<ChartFamily>()
  for (const s of data.series) families.add(resolveFamily(s))

  const scales: uPlot.Scales = { x: { time: true } }
  const axes: uPlot.Axis[] = []

  for (const family of families) {
    const scale: uPlot.Scale = { auto: true }
    const soft = SOFT_RANGES[family]
    if (soft !== undefined) {
      scale.range = {
        min: soft.min ?? {},
        max: soft.max ?? {},
      }
    }
    scales[family] = scale

    const isTemperature = family === 'temperature'
    axes.push({
      scale: family,
      side: isTemperature ? 1 : 3,
      stroke: familyColor(family),
      grid: { stroke: 'rgba(128, 128, 128, 0.15)' },
      ticks: { stroke: familyColor(family) },
      size: 48,
    })
  }

  return { scales, axes }
}
