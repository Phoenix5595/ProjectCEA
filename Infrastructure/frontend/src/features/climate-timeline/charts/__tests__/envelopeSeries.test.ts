import { describe, expect, it } from 'vitest'
import { RichTrajectoryEnvelope } from '../../api/contracts'
import type { RichTrajectoryEnvelope as Envelope } from '../../api/contracts'
import {
  buildEnvelopeSeries,
  envelopeSampleTimes,
  groupEnvelopeSegments,
  normalizeTimelineMetric,
  photoperiodIntervals,
} from '../envelopeSeries'

const WINDOW = {
  start: '2026-01-01T00:00:00.000Z',
  end: '2026-01-02T00:00:00.000Z',
  timezone: 'America/Toronto',
}

function segment(
  overrides: Partial<Record<string, unknown>> & { metric?: string; kind?: 'scheduled' | 'effective' },
): Record<string, unknown> {
  return {
    shape: 'step',
    value: 22,
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-01T01:00:00.000Z',
    metric: 'heating_setpoint',
    unit: 'C',
    trajectory_kind: 'scheduled',
    quality: 'exact',
    source: {
      mode: 'veg',
      submode: null,
      period: { period_id: 'p1', label: 'Day' },
      config_revision: 'config-1',
      draft_revision: 'draft-1',
    },
    ...overrides,
  }
}

function envelope(segments: Record<string, unknown>[]): Envelope {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Vegetation Room',
    generated_at: '2026-01-01T00:00:00.000Z',
    window: WINDOW,
    revision_scope: 'draft',
    base_config_revision: 'config-1',
    draft_revision: 'draft-1',
    segments,
    assumptions: [],
    warnings: [],
  }) as Envelope
}


describe('normalizeTimelineMetric', () => {
  it('accepts the four climate metrics and rejects light metrics', () => {
    expect(normalizeTimelineMetric('heating_setpoint')).toBe('heating_setpoint')
    expect(normalizeTimelineMetric('cooling_setpoint')).toBe('cooling_setpoint')
    expect(normalizeTimelineMetric('vpd_setpoint')).toBe('vpd_setpoint')
    expect(normalizeTimelineMetric('co2_setpoint')).toBe('co2_setpoint')
    expect(normalizeTimelineMetric('light.photoperiod')).toBeNull()
    expect(normalizeTimelineMetric('light.intensity.main')).toBeNull()
  })
})

describe('buildEnvelopeSeries', () => {
  it('samples a linear ramp on its slope: 22→20 over 30 min evaluates 21 at the midpoint', () => {
    const env = envelope([
      segment({
        shape: 'linear',
        start_value: 22,
        end_value: 20,
        start: '2026-01-01T10:00:00.000Z',
        end: '2026-01-01T10:30:00.000Z',
      }),
    ])
    const series = buildEnvelopeSeries(env, [
      Date.parse('2026-01-01T10:00:00.000Z'),
      Date.parse('2026-01-01T10:15:00.000Z'),
      Date.parse('2026-01-01T10:30:00.000Z'),
      Date.parse('2026-01-01T11:00:00.000Z'),
    ])
    const heating = series.series.get('heating_setpoint:scheduled')
    expect(heating).toEqual([22, 21, 20, null])
  })

  it('holds step segments forward and never bridges an unavailable gap', () => {
    const env = envelope([
      segment({ value: 22, start: '2026-01-01T00:00:00.000Z', end: '2026-01-01T01:00:00.000Z' }),
      segment({
        shape: 'unavailable',
        reason: 'schedule coverage is unavailable',
        start: '2026-01-01T01:00:00.000Z',
        end: '2026-01-01T02:00:00.000Z',
      }),
      segment({ value: 24, start: '2026-01-01T02:00:00.000Z', end: '2026-01-01T03:00:00.000Z' }),
    ])
    const series = buildEnvelopeSeries(env, [
      Date.parse('2026-01-01T00:30:00.000Z'),
      Date.parse('2026-01-01T01:30:00.000Z'),
      Date.parse('2026-01-01T02:30:00.000Z'),
    ])
    expect(series.series.get('heating_setpoint:scheduled')).toEqual([22, null, 24])
  })

  it('produces distinct series for scheduled and effective kinds', () => {
    const env = envelope([
      segment({ value: 22, start: '2026-01-01T00:00:00.000Z', end: '2026-01-01T01:00:00.000Z' }),
      segment({ value: 21, trajectory_kind: 'effective', start: '2026-01-01T00:00:00.000Z', end: '2026-01-01T01:00:00.000Z' }),
    ])
    const series = buildEnvelopeSeries(env, [Date.parse('2026-01-01T00:30:00.000Z')])
    expect(series.keys).toEqual(['heating_setpoint:scheduled', 'heating_setpoint:effective'])
    expect(series.series.get('heating_setpoint:scheduled')).toEqual([22])
    expect(series.series.get('heating_setpoint:effective')).toEqual([21])
  })

  it('groups the four metrics in a stable metric-then-kind order and records units', () => {
    const env = envelope([
      segment({ metric: 'co2_setpoint', unit: 'ppm', value: 900 }),
      segment({ metric: 'vpd_setpoint', unit: 'kPa', value: 1.1 }),
      segment({ metric: 'cooling_setpoint', unit: 'C', value: 28 }),
      segment({ metric: 'heating_setpoint', unit: 'C', value: 22, trajectory_kind: 'effective' }),
      segment({ metric: 'heating_setpoint', unit: 'C', value: 22 }),
    ])
    const groups = groupEnvelopeSegments(env)
    expect(groups.map((group) => `${group.metric}:${group.kind}`)).toEqual([
      'heating_setpoint:scheduled',
      'heating_setpoint:effective',
      'cooling_setpoint:scheduled',
      'vpd_setpoint:scheduled',
      'co2_setpoint:scheduled',
    ])
    expect(buildEnvelopeSeries(env, [0]).units.get('co2_setpoint')).toBe('ppm')
  })

  it('returns null before the first segment and holds the last step forward after it', () => {
    const env = envelope([
      segment({ start: '2026-01-01T05:00:00.000Z', end: '2026-01-01T06:00:00.000Z' }),
    ])
    const series = buildEnvelopeSeries(env, [
      Date.parse('2026-01-01T04:00:00.000Z'),
      Date.parse('2026-01-01T05:30:00.000Z'),
      Date.parse('2026-01-01T07:00:00.000Z'),
    ])
    expect(series.series.get('heating_setpoint:scheduled')).toEqual([null, 22, 22])
  })
})

describe('envelopeSampleTimes', () => {
  it('steps one minute across the window and ends exactly at the window end', () => {
    const times = envelopeSampleTimes({
      start: new Date(Date.parse('2026-01-01T00:00:00.000Z')),
      end: new Date(Date.parse('2026-01-01T01:00:00.000Z')),
    })
    expect(times).toHaveLength(61)
    expect(times[0]).toBe(Date.parse('2026-01-01T00:00:00.000Z'))
    expect(times.at(-1)).toBe(Date.parse('2026-01-01T01:00:00.000Z'))
  })
})

describe('photoperiodIntervals', () => {
  it('splits the window into moon and sun bands around the photoperiod times', () => {
    const intervals = photoperiodIntervals(
      { dayStartTime: '06:00', nightStartTime: '18:00' },
      Date.parse('2026-01-01T00:00:00.000Z'),
      Date.parse('2026-01-02T00:00:00.000Z'),
    )
    expect(intervals).toEqual([
      { start: Date.parse('2026-01-01T00:00:00.000Z'), end: Date.parse('2026-01-01T06:00:00.000Z'), phase: 'MOON' },
      { start: Date.parse('2026-01-01T06:00:00.000Z'), end: Date.parse('2026-01-01T18:00:00.000Z'), phase: 'SUN' },
      { start: Date.parse('2026-01-01T18:00:00.000Z'), end: Date.parse('2026-01-02T00:00:00.000Z'), phase: 'MOON' },
    ])
  })

  it('wraps a night that crosses midnight', () => {
    const intervals = photoperiodIntervals(
      { dayStartTime: '22:00', nightStartTime: '06:00' },
      Date.parse('2026-01-01T00:00:00.000Z'),
      Date.parse('2026-01-02T00:00:00.000Z'),
    )
    expect(intervals[0]).toEqual({ start: Date.parse('2026-01-01T00:00:00.000Z'), end: Date.parse('2026-01-01T06:00:00.000Z'), phase: 'SUN' })
    expect(intervals.some((interval) => interval.phase === 'SUN' && interval.start === Date.parse('2026-01-01T22:00:00.000Z'))).toBe(true)
  })

  it('renders the whole window as moon when day equals night', () => {
    const intervals = photoperiodIntervals(
      { dayStartTime: '06:00', nightStartTime: '06:00' },
      Date.parse('2026-01-01T00:00:00.000Z'),
      Date.parse('2026-01-01T12:00:00.000Z'),
    )
    expect(intervals).toEqual([
      { start: Date.parse('2026-01-01T00:00:00.000Z'), end: Date.parse('2026-01-01T12:00:00.000Z'), phase: 'MOON' },
    ])
  })
})
