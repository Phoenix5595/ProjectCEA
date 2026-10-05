import type { MonitoringManifest, TimeseriesPanelSpec } from '../config'
import { alignSeries, createPanelAlignment } from '../data'
import type { AlignInput, AlignedData } from '../data'

export interface ChartGroups {
  climate: AlignedData
  device: AlignedData
}

export interface ChartGroupAlignment {
  align(input: AlignInput): ChartGroups
}

export function timeseriesPanels(manifest: MonitoringManifest): TimeseriesPanelSpec[] {
  return manifest.panels.filter((p): p is TimeseriesPanelSpec => p.kind === 'timeseries')
}

function filterToPanel(aligned: AlignedData, panel: TimeseriesPanelSpec): AlignedData {
  const series = aligned.series.filter(
    series => panel.sources.includes(series.source) && panel.families.includes(series.family)
  )
  const keep = new Set(series.map(s => s.key))
  const bands = aligned.bands.filter(b => keep.has(b.minKey) && keep.has(b.maxKey))
  return { ...aligned, series, bands }
}

/** Split aligned data into the climate and device chart groups. */
export function splitChartGroups(
  manifest: MonitoringManifest,
  aligned: AlignedData,
  lightRegistry?: AlignInput['lightRegistry']
): ChartGroups {
  const panels = timeseriesPanels(manifest)
  const climatePanel = panels[0]
  const devicePanel = panels[1]
  const climate = climatePanel ? filterToPanel(aligned, climatePanel) : aligned
  const device = devicePanel ? filterToPanel(aligned, devicePanel) : aligned
  const lightIds = new Set(lightRegistry?.map(item => item.device_name) ?? [])
  for (const series of aligned.series) {
    if (series.source === 'light') lightIds.add(series.metric)
  }
  const filteredDevice = withoutLightOverlays(device, lightIds)
  return { climate, device: filteredDevice }
}

function withoutLightOverlays(data: AlignedData, lightIds: ReadonlySet<string>): AlignedData {
  const series = data.series.filter(candidate => {
    if (candidate.source === 'device') return !lightIds.has(candidate.metric)
    if (candidate.source === 'pid') {
      const deviceName = candidate.metric.endsWith('_pid')
        ? candidate.metric.slice(0, -4)
        : candidate.metric
      return !lightIds.has(deviceName)
    }
    return true
  })
  if (series.length === data.series.length) return data
  const keep = new Set(series.map(item => item.key))
  return {
    ...data,
    series,
    bands: data.bands.filter(band => keep.has(band.minKey) && keep.has(band.maxKey)),
  }
}

export function createChartGroupAlignment(manifest: MonitoringManifest): ChartGroupAlignment {
  const panels = timeseriesPanels(manifest)
  const climatePanel = panels[0]
  const devicePanel = panels[1]
  const climateAlignment = createPanelAlignment()
  const deviceAlignment = createPanelAlignment()

  return {
    align(input) {
      if (climatePanel === undefined || devicePanel === undefined) {
        return splitChartGroups(manifest, alignSeries(input), input.lightRegistry)
      }
      return {
        climate: climateAlignment.align({ ...input, panel: climatePanel }),
        device: deviceAlignment.align({ ...input, panel: devicePanel }),
      }
    },
  }
}
