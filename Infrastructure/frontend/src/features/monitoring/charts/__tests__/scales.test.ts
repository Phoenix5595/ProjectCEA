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

  it('preserves the historical family-axis spacing contract at every chart width', () => {
    const narrow = buildScales(makeData())
    const wide = buildScales(makeData())

    for (const axes of [narrow.axes, wide.axes]) {
      const familyAxes = axes.filter((axis) => axis.scale !== 'x')
      expect(familyAxes).toHaveLength(7)
      for (const axis of familyAxes) {
        expect(axis.label).toBeUndefined()
        expect(axis.gap).toBeUndefined()
        expect(axis.font).toBeUndefined()
        expect(axis.labelSize).toBeUndefined()
        expect(axis.size).toBe(48)
        expect(axis.ticks).toEqual({ stroke: axis.stroke })
      }
    }

    expect(narrow.axes.slice(1)).toEqual(wide.axes.slice(1))
  })

  it('preserves configured family bounds while padding every populated scale', () => {
    const { scales } = buildScales(makeData())
    const temperatureRange = requiredRange(scales.temperature?.range)
    const rhRange = requiredRange(scales.rh?.range)
    const deviceRange = requiredRange(scales.device?.range)
    const lightRange = requiredRange(scales.light?.range)
    const pressureRange = requiredRange(scales.pressure?.range)

    expect(Reflect.apply(temperatureRange, undefined, [undefined, 21, 25])).toEqual([20, 26])
    expect(Reflect.apply(temperatureRange, undefined, [undefined, 10, 50])).toEqual([0, 60])
    expect(Reflect.apply(rhRange, undefined, [undefined, 40, 96])).toEqual([20, 100])
    expect(Reflect.apply(deviceRange, undefined, [undefined, 15, 85])).toEqual([0, 100])
    expect(Reflect.apply(lightRange, undefined, [undefined, 25, 75])).toEqual([0, 100])
    expect(Reflect.apply(pressureRange, undefined, [undefined, 1013, 1013.5])).toEqual([
      1012,
      1014,
    ])
    expect(Reflect.apply(requiredRange(scales.vpd?.range), undefined, [undefined, 21, 25])).toEqual([20, 26])
    expect(Reflect.apply(requiredRange(scales.co2?.range), undefined, [undefined, 21, 25])).toEqual([20, 26])
  })

  it('adds headroom and footroom for every populated family scale', () => {
    const { scales } = buildScales(makeData())
    const observedExtrema = {
      temperature: [21, 25],
      rh: [40, 96],
      vpd: [21, 25],
      device: [15, 85],
      light: [25, 75],
      pressure: [1013, 1013.5],
      co2: [21, 25],
    } as const

    for (const family of ['temperature', 'rh', 'vpd', 'device', 'light', 'pressure', 'co2'] as const) {
      const range = requiredRange(scales[family]?.range)
      const [min, max] = Reflect.apply(range, undefined, [undefined, ...observedExtrema[family]])

      const expected = family === 'rh'
        ? [20, 100]
        : family === 'device' || family === 'light'
          ? [0, 100]
          : family === 'pressure'
            ? [1012, 1014]
            : [20, 26]
      expect([min, max]).toEqual(expected)
    }
  })

  it('applies 5% margins to effective extrema across multiple series', () => {
    const data = makeData()
    const temperature = data.series.find((series) => series.family === 'temperature')
    if (temperature === undefined) throw new Error('Temperature series is required')
    temperature.y = [20, null]
    data.series.push({
      ...temperature,
      key: seriesKey('sensor', 'temperature_max', 'max'),
      y: [null, 24],
    })

    const range = requiredRange(buildScales(data).scales.temperature?.range)
    expect(Reflect.apply(range, undefined, [undefined, 20, 24])).toEqual([18, 26])
  })

  it('pads flat and single-point displayed ranges by at least one unit', () => {
    const { scales } = buildScales(makeData())
    const flatRange = requiredRange(scales.vpd?.range)
    const singlePointRange = requiredRange(scales.co2?.range)

    expect(Reflect.apply(flatRange, undefined, [undefined, 22, 22])).toEqual([20, 24])
    expect(Reflect.apply(singlePointRange, undefined, [undefined, 0, 0])).toEqual([-1, 1])
  })

  it('adds headroom and footroom when data reaches soft, forced, or default bounds', () => {
    const data = makeData()
    const temperature = data.series.find((series) => series.family === 'temperature')
    if (temperature === undefined) throw new Error('Temperature series is required')
    temperature.presentation = { softMin: 10, softMax: 35 }

    const { scales } = buildScales(data)
    const softRange = requiredRange(scales.temperature?.range)
    const rhRange = requiredRange(scales.rh?.range)
    const deviceRange = requiredRange(scales.device?.range)
    const [softMin, softMax] = Reflect.apply(softRange, undefined, [undefined, 10, 35])
    const [rhMin, rhMax] = Reflect.apply(rhRange, undefined, [undefined, 40, 100])
    const [deviceMin, deviceMax] = Reflect.apply(deviceRange, undefined, [undefined, 0, 100])

    expect(softMin).toBeLessThan(10)
    expect(softMax).toBeGreaterThan(35)
    expect(rhMin).toBeLessThan(40)
    expect(rhMax).toBeGreaterThan(100)
    expect(deviceMin).toBeLessThan(0)
    expect(deviceMax).toBeGreaterThan(100)

    const defaultData = makeData()
    defaultData.scaleDefaults = { unit: 'celsius', softMin: 15 }
    const defaultRange = requiredRange(buildScales(defaultData).scales.temperature?.range)
    const [defaultMin, defaultMax] = Reflect.apply(defaultRange, undefined, [undefined, 15, 25])

    expect(defaultMin).toBeLessThan(15)
    expect(defaultMax).toBeGreaterThan(25)
  })

  it('honors softMin/softMax for temperature without clipping legitimate values', () => {
    const data = makeData()
    const temperature = data.series.find((series) => series.family === 'temperature')
    if (temperature === undefined) throw new Error('Temperature series is required')

    temperature.presentation = { softMin: 10, softMax: 35 }
    const { scales } = buildScales(data)
    const range = requiredRange(scales.temperature?.range)

    // Within soft bounds: display exactly the configured window
    expect(Reflect.apply(range, undefined, [undefined, 12, 22])).toEqual([10, 35])

    expect(Reflect.apply(range, undefined, [undefined, 9, 36])).toEqual([7.65, 37.35])

    // Missing initMin falls back to softMin
    expect(Reflect.apply(range, undefined, [undefined, undefined, 22])).toEqual([10, 35])

    // Missing initMax falls back to softMax, while softMin still anchors the lower bound
    expect(Reflect.apply(range, undefined, [undefined, 18, undefined])).toEqual([10, 35])

    // All missing falls back to the configured soft bounds
    expect(Reflect.apply(range, undefined, [undefined, undefined, undefined])).toEqual([10, 35])
  })

  it('adds vertical headroom around finite extrema outside soft bounds', () => {
    const data = makeData()
    const temperature = data.series.find((series) => series.family === 'temperature')
    if (temperature === undefined) throw new Error('Temperature series is required')

    temperature.presentation = { softMin: 10, softMax: 35 }
    const { scales } = buildScales(data)
    const range = requiredRange(scales.temperature?.range)
    const [min, max] = Reflect.apply(range, undefined, [undefined, 9, 36])

    expect(min).toBeLessThan(9)
    expect(max).toBeGreaterThan(36)
    expect([min, max]).toEqual([7.65, 37.35])
  })

  it('honors panel default soft bounds for the matching family', () => {
    const data = makeData()
    data.scaleDefaults = { unit: 'celsius', softMin: 15 }
    const { scales } = buildScales(data)
    const range = requiredRange(scales.temperature?.range)

    expect(Reflect.apply(range, undefined, [undefined, 21, 25])).toEqual([15, 25.2])
  })

  it('draws actual VPD series with the required three-pixel width', () => {
    const data = makeData()
    const vpd = data.series.find((series) => series.family === 'vpd')
    if (vpd === undefined) throw new Error('VPD series is required')

    vpd.presentation = { lineWidth: 3 }
    expect(buildSeries(data).find((series) => series.label === 'vpd')?.width).toBe(3)
  })

  it('keeps dot-style setpoint lanes visible', () => {
    const data = makeData()
    const vpd = data.series.find((series) => series.family === 'vpd')
    if (vpd === undefined) throw new Error('VPD series is required')

    vpd.presentation = { dash: [0, 5] }
    vpd.source = 'climate'
    const dotLane = buildSeries(data).find((series) => series.label === 'vpd')
    if (dotLane === undefined) throw new Error('VPD series is required')
    expect(dotLane.dash).toEqual([1, 5])
    expect(dotLane.cap).toBe('round')
  })
})
