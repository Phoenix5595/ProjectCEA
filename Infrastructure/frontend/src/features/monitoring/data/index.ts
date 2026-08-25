/**
 * Public barrel for the monitoring feature data-alignment layer.
 *
 * Re-exports the pure `alignSeries` transform and its input/output types so
 * the uPlot adapter (Todo 22) and tables (Todo 25) import from a single module.
 */
export { alignSeries, alignSeriesBase, applyLiveTail } from './alignSeries'
export { createPanelAlignment } from './panelAlignment'
export { decimateSeries, panelBudget, requestBudget } from './pointBudget'
export type {
  AlignInput,
  AlignedBand,
  AlignedData,
  AlignedSeries,
  MutableSeriesPresentation,
  PhotoperiodInterval,
  SeriesKey,
  SeriesKind,
  SeriesPresentation,
  SeriesRole,
  SeriesSource,
} from './alignSeries.types'
export type { BaseAlignment } from './alignSeries'
export type { PanelAlignment, PanelAlignmentCounts, PanelAlignmentInput } from './panelAlignment'
