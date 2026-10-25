/**
 * Public barrel for the history-overview feature.
 *
 * Exports the full view, the drill-down clamp helper, and the core bucket
 * picker so pages, fixture previews, and tests import from one module.
 */
export { HistoryOverviewView, HistoryOverviewChart } from './HistoryOverviewChart'
export type { HistoryOverviewViewProps, HistoryOverviewChartProps } from './HistoryOverviewChart'
export { HistoryOverviewTables } from './HistoryOverviewTables'
export type { HistoryOverviewTablesProps } from './HistoryOverviewTables'
export {
  historyOverviewBucketBounds,
  historyBucketBounds,
  chooseHistoryBucket,
  drillDownRange,
} from './buckets'
export { HistoryOverviewApi } from './api'
export type {
  HistoryBucketMetadata,
  HistoryBucketBounds,
  HistoryBucketBoundsEntry,
} from './buckets'
export { useHistoryOverview } from './useHistoryOverview'
export type { UseHistoryOverviewOptions, UseHistoryOverviewResult } from './useHistoryOverview'
export { buildHistoryOverviewAlignedData } from './aligned'
export type { HistoryOverviewAlignedOptions } from './aligned'
export {
  HistoryOverviewSensorResponse,
  HistoryOverviewControlResponse,
  HistoryOverviewPoint,
  HistoryOverviewRelayPoint,
} from './contracts'
export type { HistoryBucket, HistoryOverviewRequest } from './contracts'
