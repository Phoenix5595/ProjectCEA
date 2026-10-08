/**
 * Public barrel for the monitoring feature data-alignment layer.
 *
 * Re-exports the pure `alignSeries` transform and its input/output types so
 * the uPlot adapter (Todo 22) and tables (Todo 25) import from a single module.
 */
export { alignSeries, alignSeriesBase, applyLiveTail } from './alignSeries'
export { composePhotoperiod } from './alignSeries.series'
export { composeLightTrajectory, lightSegmentAt, lightValueAt } from './lightTrajectory'
export { createPanelAlignment } from './panelAlignment'
export { decimateSeries, panelBudget, requestBudget } from './pointBudget'
export type {
  AlignInput,
  AlignedBand,
  AlignedData,
  AlignedSeries,
  MutableSeriesPresentation,
  PhotoperiodInterval,
  LightTrajectorySegment,
  SeriesKey,
  SeriesKind,
  SeriesPresentation,
  SeriesRole,
  SeriesSource,
} from './alignSeries.types'
export type { BaseAlignment } from './alignSeries'
export type { PanelAlignment, PanelAlignmentCounts, PanelAlignmentInput } from './panelAlignment'
