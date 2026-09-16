import { describe, expect, it } from 'vitest'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'

function makeTemperatureData(): AlignedData {
  return {
    x: [1e12, 1e12 + 60_000],
    series: [
      {
        key: seriesKey('sensor', 'dry_bulb_b', 'mean'),
        label: 'dry_bulb_b',
        kind: 'sensor',
        source: 'sensor',
        metric: 'dry_bulb_b',
        family: 'temperature',
        role: 'mean',
        y: [22, null],
        origin: 'recorded',
        quality: 'exact',
        isAggregated: false,
      },
    ],
    bands: [],
    photoperiod: [],
    nowIndex: 0,
    aggregated: false,
  }
}

describe('uPlot honors enforced range functions', () => {
  it('temperature scale applies 5% displayed-range margins', async () => {
    const { buildScales } = await import('../options/scales')

    const { scales, axes } = buildScales(makeTemperatureData())
    expect(axes.find((a) => a.scale === 'temperature')?.side).toBe(3)

    expect(axes.find((axis) => axis.scale === 'temperature')?.side).toBe(3)
    const range = scales.temperature?.range
    if (typeof range !== 'function') throw new Error('Temperature range is required')
    expect(Reflect.apply(range, undefined, [undefined, 22, 25])).toEqual([21.85, 25.15])
  })
})
