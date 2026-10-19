/**
 * Public barrel for the monitoring components.
 *
 * Exposes the time-range toolbar, the semantic table/stat primitives, and
 * their pure helper modules so downstream consumers (Todo 26/27 room pages)
 * import from one module.
 */
export { HistoricalViewSwitch, TimeRangeToolbar } from './TimeRangeToolbar'
export type {
  HistoricalCurrentRunControl,
  HistoricalToolbarControl,
  HistoricalViewMode,
  HistoricalViewSwitchProps,
  TimeRangeToolbarProps,
} from './TimeRangeToolbar'
export { AbsoluteRangeForm } from './TimeRangeToolbar.inputs'
export type { AbsoluteRangeFormProps, FallFoldState } from './TimeRangeToolbar.inputs'
export {
  HISTORICAL_PRESETS,
  MAX_RANGE_MS,
  MIN_HISTORY_MS,
  MIN_RANGE_MS,
  PRESETS,
  historicalPresetRange,
  offsetLabel,
  parseUrlRange,
  serializeRange,
  validateRange,
  withSerializedRange,
} from './timeRangeToolbar.time'
export type {
  HistoricalPreset,
  MaxRangeCap,
  ParsedUrlRange,
  Preset,
  ToolbarRange,
} from './timeRangeToolbar.time'
export { SensorValueTable } from './SensorValueTable'
export type { SensorValueTableProps } from './SensorValueTable'
export { RoomAveragesTable } from './RoomAveragesTable'
export type { RoomAveragesTableProps } from './RoomAveragesTable'
export { StatisticsTable } from './StatisticsTable'
export type { StatisticsTableProps } from './StatisticsTable'
export { ChartDataTable } from './ChartDataTable'
export type { ChartDataTableProps } from './ChartDataTable'
export { MonitoringStatus } from './MonitoringStatus'
export { MonitoringFreshness } from './MonitoringFreshness'
export type { MonitoringStatusProps } from './MonitoringStatus'
export { MonitoringErrorBoundary } from './MonitoringErrorBoundary'
export {
  DEFAULT_STALE_AFTER_MS,
  FAMILY_DECIMALS,
  formatTimestamp,
  formatValue,
  isStale,
} from './tables/tableFormat'
export {
  BASE_TO_FAMILY,
  ROW_TO_BASE,
  baseLabelForAverage,
  familyForRow,
  familyForStatRow,
  sensorNameForRow,
  sensorNameForStatRow,
} from './tables/tableManifest'
