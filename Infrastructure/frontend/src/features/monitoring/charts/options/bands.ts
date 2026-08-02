/**
 * Builds uPlot bands from the aligned min/max envelope pairs.
 *
 * Each band references the hidden min and max series by their uPlot index and
 * paints the translucent envelope around the sensor mean.
 */
import uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { readToken } from './tokens'

/** Build uPlot bands from `AlignedData.bands`. */
export function buildBands(data: AlignedData): uPlot.Band[] {
  const indexByKey = new Map<string, number>()
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
