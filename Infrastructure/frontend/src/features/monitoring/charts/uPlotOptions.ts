/**
 * Structural uPlot helpers for the monitoring chart adapter.
 *
 * Todo 22 owns the imperative lifecycle (mount/destroy, resize, data, theme
 * recreation). The full themed options, plugins, legend, and interactions are
 * assembled in `options/buildOptions.ts`; this module retains the `toUPlotData`
 * converter and re-exports the themed builder so the adapter keeps one import.
 */
import uPlot from 'uplot'

import type { AlignedData } from '../data'
import { chartDataWithBands } from './options/sensorBands'

export { buildOptions } from './options/buildOptions'
export type { ChartCallbacks } from './options/buildOptions'

/** Converts the aligned shape to uPlot's `[x, ...y]` tuple. */
export function toUPlotData(data: AlignedData): uPlot.AlignedData {
  const expanded = chartDataWithBands(data).data
  return [
    expanded.x,
    ...expanded.series.map(series =>
      series.quality === 'unavailable' ? series.y.map(() => null) : series.y
    ),
  ]
}
