import uPlot from 'uplot'

import type { AlignedData, SeriesKey } from '../../data'

import { colorWithAlpha } from './colors'
import { seriesColor } from './seriesOptions'
import { readToken } from './tokens'

/** Fill for a band: the parent series colour at 15% alpha (R4). */
function bandFill(data: AlignedData, minKey: SeriesKey): string {
  const minIndex = data.series.findIndex(series => series.key === minKey)
  const minSeries = minIndex >= 0 ? data.series[minIndex] : undefined
  if (minSeries === undefined) return readToken('envelopeFill')
  const parent = data.series.find(
    series =>
      series.source === minSeries.source &&
      series.metric === minSeries.metric &&
      series.role !== 'min' &&
      series.role !== 'max'
  )
  return colorWithAlpha(seriesColor(parent ?? minSeries), 0.15)
}

export function buildBands(data: AlignedData): uPlot.Band[] {
  const indexByKey = new Map<SeriesKey, number>()
  data.series.forEach((s, i) => indexByKey.set(s.key, i + 1))

  const bands: uPlot.Band[] = []
  for (const band of data.bands) {
    const minIdx = indexByKey.get(band.minKey)
    const maxIdx = indexByKey.get(band.maxKey)
    if (minIdx === undefined || maxIdx === undefined) continue
    bands.push({
      series: [minIdx, maxIdx],
      // The first boundary is the minimum: fill upward toward the maximum.
      dir: 1,
      fill: bandFill(data, band.minKey),
    })
  }
  return bands
}
