import type { MonitoringManifest, TimeseriesPanelSpec } from '../config'
import type { AlignedData } from '../data'

export interface ChartGroups {
  climate: AlignedData
  device: AlignedData
}

export function timeseriesPanels(manifest: MonitoringManifest): TimeseriesPanelSpec[] {
  return manifest.panels.filter((p): p is TimeseriesPanelSpec => p.kind === 'timeseries')
}

function filterToPanel(aligned: AlignedData, panel: TimeseriesPanelSpec): AlignedData {
  const series = aligned.series.filter(
    (series) => panel.sources.includes(series.source) && panel.families.includes(series.family),
  )
  const keep = new Set(series.map((s) => s.key))
  const bands = aligned.bands.filter((b) => keep.has(b.minKey) && keep.has(b.maxKey))
  return { ...aligned, series, bands }
}

/** Split aligned data into the climate and device chart groups. */
export function splitChartGroups(manifest: MonitoringManifest, aligned: AlignedData): ChartGroups {
  const panels = timeseriesPanels(manifest)
  const climatePanel = panels[0]
  const devicePanel = panels[1]
  return {
    climate: climatePanel ? filterToPanel(aligned, climatePanel) : aligned,
    device: devicePanel ? filterToPanel(aligned, devicePanel) : aligned,
  }
}
