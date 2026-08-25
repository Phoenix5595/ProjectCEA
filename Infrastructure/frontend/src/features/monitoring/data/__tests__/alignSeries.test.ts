/**
 * Data-alignment tests.
 *
 * Verifies the pure `alignSeries` transform: a shared sorted-unique x axis of
 * length <= maxPoints, per-series y arrays aligned to it, sensor min/max bands,
 * step/linear control representations, null gaps preserved (never interpolated),
 * and a deterministic rebucket when the timestamp union would exceed maxPoints.
 */
import { describe, expect, it } from 'vitest'
import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  DeviceTimelineSeries,
  Origin,
  PidTimelineSeries,
  SensorSeries,
} from '../../api'
import type { MonitoringRange } from '../../state'
import { alignSeries } from '../alignSeries'
import type { AlignInput } from '../alignSeries.types'

const START = new Date('2026-08-02T11:00:00.000Z')
const END = new Date('2026-08-02T13:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')

function fixedRange(start: Date = START, end: Date = END): MonitoringRange {
  return { kind: 'fixed', start, end }
}

function sensorSeries(points: SensorSeries['points']): SensorSeries[] {
  return [
    {
      sensor: 'dry_bulb',
      node: 'front',
      unit_family: 'celsius',
      unit: '°C',
      points,
    },
  ]
}

function climateSeries(
  points: Array<{ timestamp: Date; value: number | null; origin?: Origin }>,
): ClimateTimelineSeries {
  return {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map((p) => ({
      timestamp: p.timestamp,
      value: p.value,
      nominal_value: p.value,
      metric: 'heating_setpoint',
      provenance: { origin: p.origin ?? 'recorded', quality: 'exact', is_aggregated: false },
    })),
    steps: [],
    linear: [],
  }
}

function controlResponse(
  climate: ClimateTimelineSeries[],
  overrides: Partial<ControlMonitoringResponse> = {},
): ControlMonitoringResponse {
  return {
    range: { start: START, end: END },
    runtime_snapshot_version: 1,
    cursors: [],
    flush_health: [],
    climate,
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
    ...overrides,
  }
}

describe('alignSeries', () => {
  it('aligns max Flower fixture within five thousand x points', () => {
    const dense: Array<{ timestamp: Date; value: number }> = []
    for (let i = 0; i < 6000; i++) {
      dense.push({ timestamp: new Date(START.getTime() + i * 1000), value: 22 + (i % 10) * 0.1 })
    }
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
        { timestamp: new Date('2026-08-02T11:30:00.000Z'), average: 24.8, minimum: 24.4, maximum: 25.2, sample_count: 60 },
      ]),
      controlHistory: null,
      projectionHistory: controlResponse([climateSeries(dense)]),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)

    expect(out.x.length).toBeLessThanOrEqual(5000)
    expect(out.aggregated).toBe(true)
    expect(out.x[0]).toBe(START.getTime())
    expect(out.x[out.x.length - 1]).toBe(END.getTime())
    for (const s of out.series) expect(s.y).toHaveLength(out.x.length)
  })

  it('rejects false interpolation and unbounded timestamp union', () => {
    const t0 = new Date('2026-08-02T11:00:00.000Z')
    const t1 = new Date('2026-08-02T11:15:00.000Z')
    const t2 = new Date('2026-08-02T11:30:00.000Z')
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: t0, average: 20, minimum: 19, maximum: 21, sample_count: 60 },
        { timestamp: t2, average: 24, minimum: 23, maximum: 25, sample_count: 60 },
      ]),
      controlHistory: controlResponse([
        climateSeries([
          { timestamp: t0, value: 22 },
          { timestamp: t1, value: null },
          { timestamp: t2, value: 23 },
        ]),
      ]),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(t0, t2),
      now: t2,
      maxPoints: 100,
    }

    const out = alignSeries(input)

    expect(out.aggregated).toBe(false)
    const t1Index = out.x.indexOf(t1.getTime())
    expect(t1Index).toBeGreaterThanOrEqual(0)
    const sensorMean = out.series.find((s) => s.metric === 'dry_bulb' && s.role === 'mean')
    expect(sensorMean?.y[t1Index]).toBeNull()
    const controlPoint = out.series.find((s) => s.metric === 'heating_setpoint' && s.role === 'point')
    expect(controlPoint?.y[t1Index]).toBeNull()

    const dense: Array<{ timestamp: Date; value: number }> = []
    for (let i = 0; i < 2000; i++) {
      dense.push({ timestamp: new Date(t0.getTime() + i * 1000), value: 22 })
    }
    const big = alignSeries({
      ...input,
      projectionHistory: controlResponse([climateSeries(dense)]),
      range: fixedRange(t0, new Date(t0.getTime() + 2000 * 1000)),
      maxPoints: 500,
    })
    expect(big.x.length).toBeLessThanOrEqual(500)
    expect(big.aggregated).toBe(true)
  })

  it('emits sensor bands and step/linear control series', () => {
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
      ]),
      controlHistory: controlResponse([
        {
          ...climateSeries([]),
          steps: [
            { timestamp: START, value: 22, provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false } },
          ],
          linear: [
            { start: START, end: END, start_value: 22, end_value: 24, provenance: { origin: 'projected', quality: 'estimated', is_aggregated: false } },
          ],
        },
      ]),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)

    expect(out.bands).toHaveLength(1)
    expect(out.bands[0].minKey).toContain('dry_bulb')
    expect(out.bands[0].minKey).toContain('min')
    expect(out.bands[0].maxKey).toContain('dry_bulb')
    expect(out.bands[0].maxKey).toContain('max')
    expect(out.series.some((s) => s.kind === 'step')).toBe(true)
    expect(out.series.some((s) => s.kind === 'linear')).toBe(true)
    const startIdx = out.x.indexOf(START.getTime())
    const linear = out.series.find((s) => s.kind === 'linear')
    expect(linear?.y[startIdx]).toBeCloseTo(22)
    const step = out.series.find((s) => s.kind === 'step')
    expect(step?.y[startIdx]).toBe(22)
  })

  it('keeps semantics stable when labels change', () => {
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
      ]),
      controlHistory: controlResponse([
        climateSeries([
          { timestamp: START, value: 22 },
          { timestamp: END, value: 23 },
        ]),
      ]),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const base = alignSeries(input)
    const mutated: AlignInput = {
      ...input,
      controlHistory: input.controlHistory
        ? {
            ...input.controlHistory,
            climate: input.controlHistory.climate.map((s) => ({
              ...s,
              name: `${s.name} [CHANGED]`,
            })),
          }
        : null,
    }
    const changed = alignSeries(mutated)

    expect(base.series.map((s) => s.family)).toEqual(changed.series.map((s) => s.family))
    expect(base.series.map((s) => s.role)).toEqual(changed.series.map((s) => s.role))
    expect(base.series.map((s) => s.source)).toEqual(changed.series.map((s) => s.source))
    expect(base.series.map((s) => s.metric)).toEqual(changed.series.map((s) => s.metric))
    expect(changed.series.every((s) => !s.label.includes('[CHANGED]') || s.metric === 'heating_setpoint')).toBe(true)
  })

  it('uses manifest display metadata for a matching sensor series', () => {
    const input: AlignInput = {
      series: [
        {
          ...sensorSeries([
            { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
          ])[0],
          sensor: 'dry_bulb_b',
        },
      ],
      controlHistory: null,
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
      seriesSpecs: [
        {
          name: 'dry_bulb_b',
          displayName: 'Dry Bulb (°C) - Back',
          unit: 'celsius',
          color: '#b5121b',
        },
      ],
    }

    const out = alignSeries(input)

    const dryBulb = out.series.find((series) => series.metric === 'dry_bulb_b' && series.role === 'mean')
    expect(dryBulb?.label).toBe('Dry Bulb (°C) - Back')
    expect(dryBulb?.presentation?.color).toBe('#b5121b')
  })

  it('merges recorded over projected at the same timestamp', () => {
    const t = new Date('2026-08-02T11:30:00.000Z')
    const input: AlignInput = {
      series: [],
      controlHistory: controlResponse([climateSeries([{ timestamp: t, value: 22, origin: 'recorded' }])]),
      projectionHistory: controlResponse([climateSeries([{ timestamp: t, value: 99, origin: 'projected' }])]),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)

    const idx = out.x.indexOf(t.getTime())
    const point = out.series.find((s) => s.metric === 'heating_setpoint' && s.role === 'point')
    expect(point?.y[idx]).toBe(22)
  })

  it('normalizes device state, duty, and PID output as explicit semantic series', () => {
    const t0 = new Date('2026-08-02T11:00:00.000Z')
    const t1 = new Date('2026-08-02T11:30:00.000Z')
    const t2 = new Date('2026-08-02T12:00:00.000Z')
    const prov = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false }
    const device: DeviceTimelineSeries = {
      name: 'Heater Flower',
      provenance: prov,
      points: [
        { timestamp: t0, provenance: prov, device_name: 'Heater Flower', device_state: 0, device_mode: 'AUTO', control_reason: 'schedule' },
        { timestamp: t1, provenance: prov, device_name: 'Heater Flower', device_state: 1, device_mode: 'AUTO', control_reason: 'pid' },
        { timestamp: t2, provenance: prov, device_name: 'Heater Flower', device_state: 1, device_mode: 'MANUAL', control_reason: 'override' },
      ],
      warnings: [],
    }
    const pid: PidTimelineSeries = {
      name: 'Heater Flower',
      provenance: prov,
      points: [
        { timestamp: t0, provenance: prov, device_name: 'Heater Flower', pid_output: 0, duty_cycle_percent: 0 },
        { timestamp: t1, provenance: prov, device_name: 'Heater Flower', pid_output: 25, duty_cycle_percent: 25 },
        { timestamp: t2, provenance: prov, device_name: 'Heater Flower', pid_output: 50, duty_cycle_percent: 50 },
      ],
      warnings: [],
    }
    const input: AlignInput = {
      series: [],
      controlHistory: controlResponse([], { devices: [device], pid: [pid] }),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(t0, t2),
      now: t2,
    }

    const out = alignSeries(input)

    const state = out.series.find((s) => s.source === 'device' && s.role === 'state')
    const duty = out.series.find((s) => s.source === 'device' && s.role === 'duty')
    const pidOutput = out.series.find((s) => s.source === 'pid' && s.role === 'pid_output')
    const pidDuty = out.series.find((s) => s.source === 'pid' && s.role === 'duty')
    expect(state).toBeDefined()
    expect(duty).toBeDefined()
    expect(pidOutput).toBeDefined()
    expect(pidDuty).toBeDefined()

    const i0 = out.x.indexOf(t0.getTime())
    const i2 = out.x.indexOf(t2.getTime())
    expect(state?.y[i0]).toBe(0)
    expect(state?.y[i2]).toBe(1)
    expect(duty?.y[i0]).toBe(0)
    expect(duty?.y[i2]).toBe(1)
    expect(pidOutput?.y[i0]).toBe(0)
    expect(pidOutput?.y[i2]).toBe(50)
    expect(pidDuty?.y[i0]).toBe(0)
    expect(pidDuty?.y[i2]).toBe(50)
  })

  it('has unique ascending x and matching y lengths with at-most-one source per semantic key', () => {
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
      ]),
      controlHistory: controlResponse([
        climateSeries([
          { timestamp: START, value: 22 },
          { timestamp: END, value: 23 },
        ]),
      ]),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)

    expect(new Set(out.x).size).toBe(out.x.length)
    for (let i = 1; i < out.x.length; i++) {
      expect(out.x[i]).toBeGreaterThan(out.x[i - 1])
    }
    for (const s of out.series) {
      expect(s.y).toHaveLength(out.x.length)
    }
    const keys = new Set(out.series.map((s) => s.key))
    expect(keys.size).toBe(out.series.length)
  })

})
