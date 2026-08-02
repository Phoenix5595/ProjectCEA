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

export { buildOptions } from './options/buildOptions'
export type { ChartCallbacks } from './options/buildOptions'

/** Converts the aligned shape to uPlot's `[x, ...y]` tuple. */
export function toUPlotData(data: AlignedData): uPlot.AlignedData {
  return [data.x, ...data.series.map((s) => s.y)]
}
