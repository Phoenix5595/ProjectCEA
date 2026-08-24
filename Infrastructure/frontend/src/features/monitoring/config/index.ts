/**
 * Public barrel for the monitoring feature configuration.
 *
 * Re-exports the room manifests and their typed contracts so consumers
 * (Todo 26/27 room pages) import from a single module.
 */
export { flowerManifest } from './flowerManifest'
export { vegManifest } from './vegManifest'
export type {
  MonitoringManifest,
  MonitoringPanel,
  SeriesSpec,
  TablePanelSpec,
  TimeseriesPanelSpec,
} from './manifestTypes'
