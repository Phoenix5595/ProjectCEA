/**
 * Public barrel for the monitoring chart adapter.
 *
 * Exposes the imperative uPlot wrapper, its themed options builder, the
 * external semantic legend, and the shared structural helpers so downstream
 * consumers (Todo 24 toolbar, Todo 26/27 room pages) import from one module.
 */
export { UPlotChart } from './UPlotChart'
export type { UPlotChartHandle, UPlotChartProps } from './UPlotChart'
export { buildOptions, toUPlotData } from './uPlotOptions'
export type { ChartCallbacks } from './uPlotOptions'
export { ExternalLegend } from './legend/ExternalLegend'
export type { ExternalLegendProps, LegendEntry } from './legend/ExternalLegend'
