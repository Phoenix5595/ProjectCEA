import { describe, expect, it } from 'vitest'

import type { ClimatePeriod } from '../../types/climatePeriod'
import { rampSkipThreshold, sampleMetricSeries } from '../climatePeriodTimeline'

function period(overrides: Partial<ClimatePeriod>): ClimatePeriod {
  return {
    period_name: 'A',
    start_time: '06:00',
    end_time: '12:00',
    ramp_minutes: 30,
    heating_setpoint: 22,
    cooling_setpoint: null,
    vpd_setpoint: null,
    co2_setpoint: null,
    details: '',
    ...overrides,
  }
}

describe('rampSkipThreshold', () => {
  it('mirrors the shared backend ramp policy and falls back to 0.1', () => {
    expect(rampSkipThreshold('heating')).toBe(0.1)
    expect(rampSkipThreshold('cooling')).toBe(0.1)
    expect(rampSkipThreshold('vpd')).toBe(0.01)
    expect(rampSkipThreshold('co2')).toBe(10)
    expect(rampSkipThreshold('unknown')).toBe(0.1)
  })
})

describe('sampleMetricSeries ramp parity', () => {
  it('steps to the nominal when the predecessor target is missing (never invents a start value)', () => {
    const periods = [
      period({ period_name: 'A', start_time: '06:00', end_time: '12:00' }),
      period({ period_name: 'B', start_time: '12:00', end_time: '18:00', heating_setpoint: null }),
    ]
    const series = sampleMetricSeries(periods, 'heating')
    expect(series[6 * 60]).toBe(22)
    expect(series[6 * 60 + 15]).toBe(22)
    // The NULL target period and its ramp stay null.
    expect(series[11 * 60 + 59]).toBe(22)
    expect(series[12 * 60]).toBeNull()
    expect(series[12 * 60 + 15]).toBeNull()
    expect(series[17 * 60 + 59]).toBeNull()
  })

  it('steps a strictly below-threshold delta instead of ramping it', () => {
    const periods = [
      period({ period_name: 'A', start_time: '06:00', end_time: '12:00' }),
      period({ period_name: 'B', start_time: '12:00', end_time: '18:00', heating_setpoint: 22.05 }),
    ]
    const series = sampleMetricSeries(periods, 'heating')
    expect(series[12 * 60]).toBe(22.05)
    expect(series[12 * 60 + 15]).toBe(22.05)
  })

  it('still ramps when the delta equals the threshold', () => {
    const periods = [
      period({ period_name: 'A', start_time: '06:00', end_time: '12:00' }),
      period({ period_name: 'B', start_time: '12:00', end_time: '18:00', heating_setpoint: 22.1 }),
    ]
    const series = sampleMetricSeries(periods, 'heating')
    expect(series[12 * 60]).toBe(22)
    expect(series[12 * 60 + 15]).toBeCloseTo(22.05, 12)
    expect(series[12 * 60 + 30]).toBe(22.1)
  })

  it('applies the vpd and co2 thresholds', () => {
    const periods = [
      period({ period_name: 'A', start_time: '06:00', end_time: '12:00', vpd_setpoint: 1.1, co2_setpoint: 800 }),
      period({
        period_name: 'B',
        start_time: '12:00',
        end_time: '18:00',
        vpd_setpoint: 1.105,
        co2_setpoint: 805,
      }),
    ]
    expect(sampleMetricSeries(periods, 'vpd')[12 * 60 + 15]).toBe(1.105)
    expect(sampleMetricSeries(periods, 'co2')[12 * 60 + 15]).toBe(805)

    const equality = [
      period({ period_name: 'A', start_time: '06:00', end_time: '12:00', co2_setpoint: 800 }),
      period({ period_name: 'B', start_time: '12:00', end_time: '18:00', co2_setpoint: 810 }),
    ]
    expect(sampleMetricSeries(equality, 'co2')[12 * 60]).toBe(800)
    expect(sampleMetricSeries(equality, 'co2')[12 * 60 + 30]).toBe(810)
  })
})
