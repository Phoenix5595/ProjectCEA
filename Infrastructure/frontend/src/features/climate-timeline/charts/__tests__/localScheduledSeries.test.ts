import { describe, expect, it } from 'vitest'

import type { ClimatePeriod } from '../../../../types/climatePeriod'
import { buildLocalScheduledSeries, type LocalScheduledSeries } from '../dragInteraction'

const ms = (iso: string): number => Date.parse(iso)

function period(overrides: Partial<ClimatePeriod>): ClimatePeriod {
  return {
    period_name: 'Day',
    start_time: '06:00',
    end_time: '18:00',
    ramp_minutes: 30,
    heating_setpoint: 22,
    cooling_setpoint: null,
    vpd_setpoint: null,
    co2_setpoint: null,
    details: '',
    ...overrides,
  }
}

function seriesFor(periods: ClimatePeriod[], startIso: string, endIso: string) {
  return buildLocalScheduledSeries(periods, {
    start: new Date(ms(startIso)),
    end: new Date(ms(endIso)),
  })
}

function valueAt(
  series: LocalScheduledSeries,
  metric: 'heating' | 'cooling' | 'vpd' | 'co2',
  instantMs: number
): number | null {
  const index = series.sampleTimes.indexOf(instantMs)
  if (index === -1) throw new Error('instant is not on the sample grid')
  return series.series.get(metric)?.[index] ?? null
}

const TILED = [
  period({ period_name: 'Day', start_time: '06:00', end_time: '18:00' }),
  period({ period_name: 'Night', start_time: '18:00', end_time: '06:00', ramp_minutes: 0, heating_setpoint: 18 }),
]

describe('buildLocalScheduledSeries', () => {
  it('resolves stored clocks to EST winter instants and ramps from the cyclic predecessor', () => {
    const series = seriesFor(TILED, '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    // Window covers Toronto 2025-12-31 19:00 → 2026-01-01 19:00 (EST = UTC-5).
    expect(valueAt(series, 'heating', ms('2026-01-01T00:00:00Z'))).toBe(18)
    expect(valueAt(series, 'heating', ms('2026-01-01T11:00:00Z'))).toBe(18)
    expect(valueAt(series, 'heating', ms('2026-01-01T11:15:00Z'))).toBe(20)
    expect(valueAt(series, 'heating', ms('2026-01-01T11:30:00Z'))).toBe(22)
    expect(valueAt(series, 'heating', ms('2026-01-01T17:00:00Z'))).toBe(22)
    expect(valueAt(series, 'heating', ms('2026-01-01T23:00:00Z'))).toBe(18)
    expect(valueAt(series, 'heating', ms('2026-01-02T00:00:00Z'))).toBeNull()
  })

  it('resolves the same stored clocks to EDT summer instants', () => {
    const series = seriesFor(TILED, '2026-07-01T00:00:00Z', '2026-07-02T00:00:00Z')
    expect(valueAt(series, 'heating', ms('2026-07-01T00:00:00Z'))).toBe(18)
    expect(valueAt(series, 'heating', ms('2026-07-01T10:00:00Z'))).toBe(18)
    expect(valueAt(series, 'heating', ms('2026-07-01T10:15:00Z'))).toBe(20)
    expect(valueAt(series, 'heating', ms('2026-07-01T10:30:00Z'))).toBe(22)
    expect(valueAt(series, 'heating', ms('2026-07-01T22:00:00Z'))).toBe(18)
  })

  it('seeds a gap-forward occurrence ramp at the resolved start, not wall elapsed minutes', () => {
    // 02:30 does not exist on 2026-03-08; the occurrence starts at 03:30 EDT
    // (07:30Z). Ramp minute 0 is at 07:30Z — a wall-minute table would claim
    // 60 elapsed minutes there.
    const periods = [
      period({ period_name: 'Early', start_time: '02:30', end_time: '06:00' }),
      period({
        period_name: 'Night',
        start_time: '22:00',
        end_time: '02:30',
        ramp_minutes: 0,
        heating_setpoint: 20,
      }),
    ]
    const series = seriesFor(periods, '2026-03-08T00:00:00Z', '2026-03-09T00:00:00Z')
    expect(valueAt(series, 'heating', ms('2026-03-08T04:00:00Z'))).toBe(20)
    expect(valueAt(series, 'heating', ms('2026-03-08T07:30:00Z'))).toBe(20)
    expect(valueAt(series, 'heating', ms('2026-03-08T07:45:00Z'))).toBe(21)
    expect(valueAt(series, 'heating', ms('2026-03-08T08:00:00Z'))).toBe(22)
  })

  it('measures fold-day ramp elapsed minutes absolutely from the first occurrence', () => {
    // 01:00 occurs twice on 2026-11-01 (EDT then EST); the ramp runs from the
    // first (05:00Z), so the second wall 01:30 (06:30Z) is 90 absolute minutes
    // in — a wall-minute table would claim 30.
    const periods = [
      period({ period_name: 'Dawn', start_time: '01:00', end_time: '03:00', ramp_minutes: 120 }),
      period({
        period_name: 'Night',
        start_time: '22:00',
        end_time: '01:00',
        ramp_minutes: 0,
        heating_setpoint: 20,
      }),
    ]
    const series = seriesFor(periods, '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z')
    expect(valueAt(series, 'heating', ms('2026-11-01T05:00:00Z'))).toBe(20)
    expect(valueAt(series, 'heating', ms('2026-11-01T06:00:00Z'))).toBe(21)
    expect(valueAt(series, 'heating', ms('2026-11-01T06:30:00Z'))).toBe(21.5)
    expect(valueAt(series, 'heating', ms('2026-11-01T07:00:00Z'))).toBe(22)
    expect(valueAt(series, 'heating', ms('2026-11-01T07:30:00Z'))).toBe(22)
  })

  it('renders an all-day equal-clock constant row across the whole window', () => {
    const constant = period({
      period_name: 'Constant',
      start_time: '00:00',
      end_time: '00:00',
      ramp_minutes: 0,
      heating_setpoint: 21,
    })
    const series = seriesFor([constant], '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    expect(valueAt(series, 'heating', ms('2026-01-01T00:00:00Z'))).toBe(21)
    expect(valueAt(series, 'heating', ms('2026-01-01T12:00:00Z'))).toBe(21)
    expect(valueAt(series, 'heating', ms('2026-01-01T23:59:00Z'))).toBe(21)
    expect(valueAt(series, 'heating', ms('2026-01-02T00:00:00Z'))).toBeNull()
  })

  it('keeps a NULL target null through its ramp and steps a successor with a missing prior target', () => {
    const periods = [
      period({ period_name: 'Day', start_time: '06:00', end_time: '12:00', heating_setpoint: null }),
      period({ period_name: 'Night', start_time: '12:00', end_time: '22:00' }),
    ]
    const series = seriesFor(periods, '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    expect(valueAt(series, 'heating', ms('2026-01-01T11:00:00Z'))).toBeNull()
    expect(valueAt(series, 'heating', ms('2026-01-01T11:15:00Z'))).toBeNull()
    expect(valueAt(series, 'heating', ms('2026-01-01T16:59:00Z'))).toBeNull()
    expect(valueAt(series, 'heating', ms('2026-01-01T17:00:00Z'))).toBe(22)
    expect(valueAt(series, 'heating', ms('2026-01-01T17:15:00Z'))).toBe(22)
  })

  it('steps strictly-below-threshold deltas and still ramps equality deltas', () => {
    const skip = [
      period({ period_name: 'Day', start_time: '06:00', end_time: '12:00' }),
      period({
        period_name: 'Night',
        start_time: '12:00',
        end_time: '22:00',
        heating_setpoint: 22.05,
      }),
    ]
    const skipSeries = seriesFor(skip, '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    expect(valueAt(skipSeries, 'heating', ms('2026-01-01T17:00:00Z'))).toBe(22.05)
    expect(valueAt(skipSeries, 'heating', ms('2026-01-01T17:15:00Z'))).toBe(22.05)

    const ramp = [
      period({ period_name: 'Day', start_time: '06:00', end_time: '12:00' }),
      period({
        period_name: 'Night',
        start_time: '12:00',
        end_time: '22:00',
        heating_setpoint: 22.1,
      }),
    ]
    const rampSeries = seriesFor(ramp, '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    expect(valueAt(rampSeries, 'heating', ms('2026-01-01T17:00:00Z'))).toBe(22)
    expect(valueAt(rampSeries, 'heating', ms('2026-01-01T17:15:00Z'))).toBeCloseTo(22.05, 12)
    expect(valueAt(rampSeries, 'heating', ms('2026-01-01T17:30:00Z'))).toBe(22.1)
  })

  it('applies per-metric thresholds (vpd 0.01, co2 10)', () => {
    const periods = [
      period({ period_name: 'Day', start_time: '06:00', end_time: '12:00', vpd_setpoint: 1.1, co2_setpoint: 800 }),
      period({
        period_name: 'Night',
        start_time: '12:00',
        end_time: '22:00',
        vpd_setpoint: 1.105,
        co2_setpoint: 805,
      }),
    ]
    const series = seriesFor(periods, '2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')
    expect(valueAt(series, 'vpd', ms('2026-01-01T17:00:00Z'))).toBe(1.105)
    expect(valueAt(series, 'co2', ms('2026-01-01T17:00:00Z'))).toBe(805)
  })

  it('returns no numeric points without periods and stays null outside coverage', () => {
    const series = seriesFor([], '2026-01-01T00:00:00Z', '2026-01-01T02:00:00Z')
    expect(series.sampleTimes).toHaveLength(2 * 60 + 1)
    expect(series.series.get('heating')?.every(value => value === null)).toBe(true)
  })
})
