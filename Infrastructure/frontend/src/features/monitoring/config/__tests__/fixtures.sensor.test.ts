import { describe, expect, it } from 'vitest'
import { sensorRangeFixture } from '../fixtures'
import { MonitoringResponse } from '../../api/contracts/sensor'
import { alignSeries } from '../../data/alignSeries'

describe('sensorRangeFixture', () => {
  it('includes the canonical Flower wet-bulb history series', () => {
    const response = MonitoringResponse.parse(sensorRangeFixture(
      'Flower Room',
      '2026-08-02T11:00:00.000Z',
      '2026-08-02T12:00:00.000Z',
    ))

    expect(response.series.map((series) => series.sensor)).toEqual(expect.arrayContaining([
      'wet_bulb_f',
      'wet_bulb_b',
    ]))

    const aligned = alignSeries({
      series: response.series,
      controlHistory: null,
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: {
        kind: 'fixed',
        start: new Date('2026-08-02T11:00:00.000Z'),
        end: new Date('2026-08-02T12:00:00.000Z'),
      },
      now: new Date('2026-08-02T12:00:00.000Z'),
    })

    expect(aligned.series.map((series) => series.metric)).toEqual(expect.arrayContaining([
      'wet_bulb_f',
      'wet_bulb_b',
    ]))
  })
})
