import { describe, expect, it } from 'vitest'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { buildScales } from '../options/scales'
import { buildSeries } from '../options/seriesOptions'

function makeData(): AlignedData {
  const families = ['temperature', 'rh', 'vpd', 'device', 'light', 'pressure', 'co2'] as const
  return {
    x: [1, 2],
    series: families.map((family) => ({
      key: seriesKey('sensor', family, 'mean'),
      label: family,
      kind: 'sensor',
      source: 'sensor',
      metric: family,
      family,
      role: 'mean',
      y: [21, 25],
      origin: 'recorded',
      quality: 'exact',
      isAggregated: false,
    })),
    bands: [],
    photoperiod: [],
    nowIndex: 1,
    aggregated: false,
  }
}

function requiredRange(range: ReturnType<typeof buildScales>['scales'][string]['range']) {
  if (typeof range !== 'function') throw new Error('Expected configured range')
  return range
}

describe('buildScales', () => {
  it('places temperature on the left and every other family on the right', () => {
    const { axes } = buildScales(makeData())

    expect(axes.find((axis) => axis.scale === 'temperature')?.side).toBe(3)
    for (const family of ['rh', 'vpd', 'device', 'light', 'pressure', 'co2']) {
      expect(axes.find((axis) => axis.scale === family)?.side).toBe(1)
    }
  })

  it('forces configured family bounds while leaving unconfigured scales automatic', () => {
    const { scales } = buildScales(makeData())
    const temperatureRange = requiredRange(scales.temperature?.range)
    const rhRange = requiredRange(scales.rh?.range)
    const deviceRange = requiredRange(scales.device?.range)
    const lightRange = requiredRange(scales.light?.range)
    const pressureRange = requiredRange(scales.pressure?.range)

    // owner rule: temperature axis pads 10 units beyond the data/setpoint extent
    expect(Reflect.apply(temperatureRange, undefined, [undefined, 21, 25])).toEqual([11, 35])
    expect(Reflect.apply(temperatureRange, undefined, [undefined, 10, 50])).toEqual([0, 60])
    expect(Reflect.apply(rhRange, undefined, [undefined, 40, 96])).toEqual([40, 100])
    expect(Reflect.apply(deviceRange, undefined, [undefined, 15, 85])).toEqual([0, 100])
    expect(Reflect.apply(lightRange, undefined, [undefined, 25, 75])).toEqual([0, 100])
    expect(Reflect.apply(pressureRange, undefined, [undefined, 1013, 1013.5])).toEqual([
      1012,
      1014,
    ])
    expect(scales.vpd?.range).toBeUndefined()
    expect(scales.co2?.range).toBeUndefined()
  })

  it('draws actual VPD series with the required three-pixel width', () => {
    const data = makeData()
    const vpd = data.series.find((series) => series.family === 'vpd')
    if (vpd === undefined) throw new Error('VPD series is required')

    vpd.presentation = { lineWidth: 3 }
    expect(buildSeries(data).find((series) => series.label === 'vpd')?.width).toBe(3)
  })
})
