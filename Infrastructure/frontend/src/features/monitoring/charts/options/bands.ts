import uPlot from 'uplot'
import type { AlignedData, SeriesKey } from '../../data'
import { readToken } from './tokens'

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
      fill: readToken('envelopeFill'),
    })
  }
  return bands
}
