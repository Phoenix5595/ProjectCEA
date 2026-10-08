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
  LightTimelineSeries,
  Origin,
  PhotoperiodTimelinePoint,
  PidTimelineSeries,
  SensorSeries,
} from '../../api'
import type { MonitoringRange } from '../../state'
import { alignSeries, alignSeriesBase, applyLiveTail } from '../alignSeries'
import { mergeControlSeries } from '../alignSeries.control'
import { composePhotoperiod } from '../alignSeries.series'
import { seriesKey } from '../alignSeries.types'
import type { AlignInput } from '../alignSeries.types'
import { lightValueAt } from '../lightTrajectory'

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
  points: Array<{ timestamp: Date; value: number | null; origin?: Origin }>
): ClimateTimelineSeries {
  return {
    name: 'Heating Setpoint',
    provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
    projection: null,
    warnings: [],
    points: points.map(p => ({
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
  overrides: Partial<ControlMonitoringResponse> = {}
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
        {
          timestamp: new Date('2026-08-02T11:30:00.000Z'),
          average: 24.8,
          minimum: 24.4,
          maximum: 25.2,
          sample_count: 60,
        },
      ]),
      controlHistory: null,
      projectionHistory: controlResponse([climateSeries(dense)]),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
      maxPoints: 5_000,
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
    const sensorMean = out.series.find(s => s.metric === 'dry_bulb' && s.role === 'mean')
    expect(sensorMean?.y[t1Index]).toBeNull()
    const controlPoint = out.series.find(s => s.metric === 'heating_setpoint' && s.role === 'point')
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
            {
              timestamp: START,
              value: 22,
              provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
            },
          ],
          linear: [
            {
              start: START,
              end: END,
              start_value: 22,
              end_value: 24,
              provenance: { origin: 'projected', quality: 'estimated', is_aggregated: false },
            },
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
    expect(out.series.some(s => s.kind === 'step')).toBe(true)
    const startIdx = out.x.indexOf(START.getTime())
    const step = out.series.find(s => s.kind === 'step')
    expect(step?.y[startIdx]).toBe(22)
  })

  it('emits one step series when a setpoint has both points and steps', () => {
    const input: AlignInput = {
      series: [],
      controlHistory: controlResponse([
        {
          ...climateSeries([{ timestamp: START, value: 22 }]),
          steps: [
            {
              timestamp: START,
              value: 22,
              provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
            },
          ],
        },
      ]),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const setpointSeries = alignSeries(input).series.filter(
      series => series.metric === 'heating_setpoint'
    )

    expect(setpointSeries).toHaveLength(1)
    expect(setpointSeries[0]?.role).toBe('step')
  })

  it('assigns co2_setpoint to the co2 family instead of temperature', () => {
    const input: AlignInput = {
      series: [],
      controlHistory: controlResponse([climateSeries([{ timestamp: START, value: 600 }])], {
        climate: [
          {
            name: 'CO2 Setpoint',
            provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
            projection: null,
            warnings: [],
            points: [
              {
                timestamp: START,
                value: 600,
                nominal_value: 600,
                metric: 'co2_setpoint',
                provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
              },
            ],
            steps: [],
            linear: [],
          },
        ],
      }),
      projectionHistory: null,
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)
    const co2Point = out.series.find(s => s.metric === 'co2_setpoint')
    expect(co2Point).toBeDefined()
    expect(co2Point?.family).toBe('co2')
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
            climate: input.controlHistory.climate.map(s => ({
              ...s,
              name: `${s.name} [CHANGED]`,
            })),
          }
        : null,
    }
    const changed = alignSeries(mutated)

    expect(base.series.map(s => s.family)).toEqual(changed.series.map(s => s.family))
    expect(base.series.map(s => s.role)).toEqual(changed.series.map(s => s.role))
    expect(base.series.map(s => s.source)).toEqual(changed.series.map(s => s.source))
    expect(base.series.map(s => s.metric)).toEqual(changed.series.map(s => s.metric))
    expect(
      changed.series.every(s => !s.label.includes('[CHANGED]') || s.metric === 'heating_setpoint')
    ).toBe(true)
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

    const dryBulb = out.series.find(
      series => series.metric === 'dry_bulb_b' && series.role === 'mean'
    )
    expect(dryBulb?.label).toBe('Dry Bulb (°C) - Back')
    expect(dryBulb?.presentation?.color).toBe('#b5121b')
  })

  it('merges recorded over projected at the same timestamp', () => {
    const t = new Date('2026-08-02T11:30:00.000Z')
    const input: AlignInput = {
      series: [],
      controlHistory: controlResponse([
        climateSeries([{ timestamp: t, value: 22, origin: 'recorded' }]),
      ]),
      projectionHistory: controlResponse([
        climateSeries([{ timestamp: t, value: 99, origin: 'projected' }]),
      ]),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
    }

    const out = alignSeries(input)

    const idx = out.x.indexOf(t.getTime())
    const step = out.series.find(s => s.metric === 'heating_setpoint' && s.role === 'step')
    expect(step?.y[idx]).toBe(22)
  })

  it('continues a recorded setpoint into the projected scheduled trajectory', () => {
    const recorded = controlResponse([
      climateSeries([
        { timestamp: START, value: 22 },
        { timestamp: NOW, value: 23 },
      ]),
    ])
    const scheduled = {
      ...climateSeries([]),
      trajectory_kind: 'scheduled' as const,
      linear: [
        {
          start: NOW,
          end: END,
          start_value: 23,
          end_value: 25,
          provenance: {
            origin: 'projected' as const,
            quality: 'estimated' as const,
            is_aggregated: false,
          },
        },
      ],
    }

    const merged = mergeControlSeries(recorded, controlResponse([scheduled]))

    expect(merged).toHaveLength(1)
    expect(merged[0]?.trajectoryKind).toBe('scheduled')
    expect(merged[0]?.steps.map(step => step.value)).toEqual([22, 23])
    expect(merged[0]?.linear[0]?.startValue).toBe(23)
  })

  it('normalizes device state and preserves true PID output and duty series', () => {
    const t0 = new Date('2026-08-02T11:00:00.000Z')
    const t1 = new Date('2026-08-02T11:30:00.000Z')
    const t2 = new Date('2026-08-02T12:00:00.000Z')
    const prov = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false }
    const device: DeviceTimelineSeries = {
      name: 'Heater Flower',
      provenance: prov,
      points: [
        {
          timestamp: t0,
          provenance: prov,
          device_name: 'Heater Flower',
          device_state: 0,
          device_mode: 'AUTO',
          control_reason: 'schedule',
        },
        {
          timestamp: t1,
          provenance: prov,
          device_name: 'Heater Flower',
          device_state: 1,
          device_mode: 'AUTO',
          control_reason: 'pid',
        },
        {
          timestamp: t2,
          provenance: prov,
          device_name: 'Heater Flower',
          device_state: 1,
          device_mode: 'MANUAL',
          control_reason: 'override',
        },
      ],
      warnings: [],
    }
    const pid: PidTimelineSeries = {
      name: 'Heater Flower',
      provenance: prov,
      points: [
        {
          timestamp: t0,
          provenance: prov,
          device_name: 'Heater Flower',
          pid_output: 0,
          duty_cycle_percent: 0,
        },
        {
          timestamp: t1,
          provenance: prov,
          device_name: 'Heater Flower',
          pid_output: 25,
          duty_cycle_percent: 25,
        },
        {
          timestamp: t2,
          provenance: prov,
          device_name: 'Heater Flower',
          pid_output: 50,
          duty_cycle_percent: 50,
        },
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

    const state = out.series.find(s => s.source === 'device' && s.role === 'state')
    const pidOutput = out.series.find(s => s.source === 'pid' && s.role === 'pid_output')
    const pidDuty = out.series.find(s => s.source === 'pid' && s.role === 'duty')
    expect(state).toBeDefined()
    expect(pidOutput).toBeDefined()
    expect(pidDuty).toBeDefined()

    const i0 = out.x.indexOf(t0.getTime())
    const i2 = out.x.indexOf(t2.getTime())
    expect(state?.y[i0]).toBe(0)
    expect(state?.y[i2]).toBe(1)
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
    const keys = new Set(out.series.map(s => s.key))
    expect(keys.size).toBe(out.series.length)
  })

  it('keeps a fixed window fixed even when projection history extends beyond it', () => {
    const projectionEnd = new Date('2026-08-03T13:00:00.000Z')
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
      ]),
      controlHistory: null,
      projectionHistory: controlResponse([], { range: { start: START, end: projectionEnd } }),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
      maxPoints: 100,
    }

    const out = alignSeries(input)
    const last = out.x[out.x.length - 1]
    const futureWidth = last - END.getTime()
    const recordedWidth = END.getTime() - START.getTime()

    expect(out.aggregated).toBe(false)
    expect(futureWidth).toBe(0)
    expect(recordedWidth).toBeGreaterThan(0)
    expect(last).toBe(END.getTime())
  })

  it('keeps a fixed window fixed when projection history is nearby', () => {
    const projectionEnd = new Date(END.getTime() + 5 * 60 * 1000)
    const input: AlignInput = {
      series: sensorSeries([
        { timestamp: START, average: 24.5, minimum: 24.1, maximum: 24.9, sample_count: 60 },
      ]),
      controlHistory: null,
      projectionHistory: controlResponse([], { range: { start: START, end: projectionEnd } }),
      photoperiod: [],
      live: [],
      range: fixedRange(),
      now: NOW,
      maxPoints: 100,
    }

    const out = alignSeries(input)
    const last = out.x[out.x.length - 1]

    expect(out.aggregated).toBe(false)
    expect(last).toBe(END.getTime())
  })
  it('keeps a six-hour recorded OFF interval flat until the next intensity step', () => {
    const offAt = START
    const onAt = new Date(offAt.getTime() + 6 * 60 * 60 * 1000)
    const end = new Date(onAt.getTime() + 60 * 60 * 1000)
    const recorded = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: true }
    const history = controlResponse([], {
      range: { start: offAt, end },
      lights: [{
        name: 'light_f_1', metric: 'light_f_1', provenance: recorded, warnings: [],
        points: [], linear: [],
        steps: [
          { timestamp: offAt, value: 0, provenance: recorded },
          { timestamp: onAt, value: 10, provenance: recorded },
        ],
      }],
    })
    const output = alignSeries({
      series: [], live: [], controlHistory: history, projectionHistory: null, photoperiod: [],
      range: fixedRange(offAt, end), now: end, maxPoints: 2,
    })
    const light = output.series.find(series => series.source === 'light')
    expect(light?.kind).toBe('step')
    expect([
      offAt.getTime(), offAt.getTime() + 3 * 60 * 60 * 1000, onAt.getTime() - 1,
      onAt.getTime(), onAt.getTime() + 10 * 60 * 1000, end.getTime(),
    ].map(time => lightValueAt(light?.lightTrajectory ?? [], time))).toEqual([0, 0, 0, 10, 10, null])
  })

  it('keeps one exact light trajectory when the aligned grid is coarsened and extended live', () => {
    const t0 = START.getTime()
    const at = (offset: number): Date => new Date(t0 + offset)
    const recorded = {
      origin: 'recorded' as const,
      quality: 'exact' as const,
      is_aggregated: false,
    }
    const projected = {
      origin: 'projected' as const,
      quality: 'estimated' as const,
      is_aggregated: false,
    }
    const unavailable = { ...projected, quality: 'unavailable' as const }
    const historyLight: LightTimelineSeries = {
      name: 'light_f_1',
      metric: 'light_f_1',
      provenance: recorded,
      warnings: [],
      points: [
        {
          timestamp: at(0),
          value: 40,
          nominal_value: 40,
          device_name: 'light_f_1',
          provenance: recorded,
        },
      ],
      steps: [],
      linear: [],
    }
    const projectedLight: LightTimelineSeries = {
      name: 'light_f_1',
      metric: 'light.intensity.light_f_1',
      trajectory_kind: 'effective',
      provenance: projected,
      warnings: [],
      points: [],
      steps: [
        { timestamp: at(10), value: 40, provenance: projected },
        { timestamp: at(40), value: null, provenance: unavailable },
        { timestamp: at(50), value: 0, provenance: projected },
      ],
      linear: [
        {
          start: at(20),
          end: at(30),
          start_value: 40,
          end_value: 80,
          provenance: projected,
        },
      ],
    }
    const history = controlResponse([], {
      range: { start: at(0), end: at(10) },
      lights: [historyLight],
    })
    const projection = controlResponse([], {
      range: { start: at(10), end: at(60) },
      lights: [projectedLight],
    })
    const input: AlignInput = {
      series: [],
      controlHistory: history,
      projectionHistory: projection,
      photoperiod: [],
      live: [],
      range: fixedRange(at(0), at(60)),
      now: at(15),
      maxPoints: 2,
    }

    const aligned = alignSeries(input)
    const lights = aligned.series.filter(series => series.source === 'light')
    const light = lights[0]

    expect(aligned.aggregated).toBe(true)
    expect(aligned.x).toHaveLength(2)
    expect(lights.map(series => series.key)).toEqual([
      seriesKey('light', 'light_f_1', 'linear'),
    ])
    expect(light?.lightTrajectory).toHaveLength(6)
    expect(light?.label).toBe('light_f_1 - Intensity')
    expect(
      [15, 25, 35, 45, 55, 60].map(time =>
        lightValueAt(light?.lightTrajectory ?? [], t0 + time)
      )
    ).toEqual([40, 60, 80, null, 0, null])

    const liveBase = alignSeriesBase({
      ...input,
      range: { kind: 'live', duration: 60 },
      now: at(10),
      maxPoints: 100,
    })
    const liveNow = at(15)
    const liveTail = applyLiveTail(
      liveBase,
      [{ sensor: 'unused_sensor', timestamp: liveNow, value: 1 }],
      liveNow
    )
    const liveLight = liveTail.series.find(series => series.key === seriesKey('light', 'light_f_1', 'linear'))

    expect(liveTail.x[liveTail.nowIndex]).toBe(liveNow.getTime())
    expect(liveLight?.y[liveTail.nowIndex]).toBe(40)
  })

  describe('photoperiod composition', () => {
    const time = (value: string): number => Date.parse(value)
    const point = (
      timestamp: string,
      phase: PhotoperiodTimelinePoint['phase'],
      origin: Origin = 'recorded'
    ): PhotoperiodTimelinePoint => ({
      timestamp: new Date(timestamp),
      phase,
      provenance: {
        origin,
        quality:
          phase === 'UNKNOWN' ? 'unavailable' : origin === 'recorded' ? 'exact' : 'estimated',
        is_aggregated: false,
      },
    })
    const interval = (
      start: string,
      end: string,
      phase: PhotoperiodTimelinePoint['phase']
    ) => ({ start: time(start), end: time(end), phase })

    it('uses a recorded predecessor in an old fixed range, not a retained future publication', () => {
      const history = controlResponse([], {
        range: {
          start: new Date('2026-08-20T08:00:00.000Z'),
          end: new Date('2026-08-20T11:00:00.000Z'),
        },
        photoperiod: [
          point('2026-08-20T08:00:00.000Z', 'SUN'),
          point('2026-08-20T09:30:00.000Z', 'MOON'),
        ],
      })
      const projection = controlResponse([], {
        range: {
          start: new Date('2026-08-20T12:00:00.000Z'),
          end: new Date('2026-08-20T13:00:00.000Z'),
        },
        photoperiod: [point('2026-08-20T12:00:00.000Z', 'SUN', 'projected')],
      })

      const output = alignSeries({
        series: [],
        controlHistory: history,
        projectionHistory: projection,
        photoperiod: history.photoperiod,
        live: [],
        range: fixedRange(
          new Date('2026-08-20T09:00:00.000Z'),
          new Date('2026-08-20T10:00:00.000Z')
        ),
        now: new Date('2026-08-20T12:00:00.000Z'),
      })

      expect(output.photoperiod).toEqual([
        interval('2026-08-20T09:00:00.000Z', '2026-08-20T09:30:00.000Z', 'SUN'),
        interval('2026-08-20T09:30:00.000Z', '2026-08-20T10:00:00.000Z', 'MOON'),
      ])
    })

    it('preserves an explicit recorded UNKNOWN span between known phases', () => {
      expect(
        composePhotoperiod(
          [
            point('2026-08-20T08:00:00.000Z', 'SUN'),
            point('2026-08-20T10:00:00.000Z', 'UNKNOWN'),
            point('2026-08-20T11:00:00.000Z', 'MOON'),
          ],
          [],
          time('2026-08-20T13:00:00.000Z'),
          Number.POSITIVE_INFINITY,
          Number.NEGATIVE_INFINITY,
          time('2026-08-20T09:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z'),
          time('2026-08-20T12:30:00.000Z')
        )
      ).toEqual([
        interval('2026-08-20T09:00:00.000Z', '2026-08-20T10:00:00.000Z', 'SUN'),
        interval('2026-08-20T10:00:00.000Z', '2026-08-20T11:00:00.000Z', 'UNKNOWN'),
        interval('2026-08-20T11:00:00.000Z', '2026-08-20T12:00:00.000Z', 'MOON'),
      ])
    })

    it('keeps a future-only publication inside its own window and leaves gaps UNKNOWN', () => {
      expect(
        composePhotoperiod(
          [],
          [
            point('2026-08-20T12:15:00.000Z', 'SUN', 'projected'),
            point('2026-08-20T12:45:00.000Z', 'MOON', 'projected'),
          ],
          Number.NEGATIVE_INFINITY,
          time('2026-08-20T12:30:00.000Z'),
          time('2026-08-20T13:30:00.000Z'),
          time('2026-08-20T11:00:00.000Z'),
          time('2026-08-20T14:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z')
        )
      ).toEqual([
        interval('2026-08-20T11:00:00.000Z', '2026-08-20T12:45:00.000Z', 'UNKNOWN'),
        interval('2026-08-20T12:45:00.000Z', '2026-08-20T13:30:00.000Z', 'MOON'),
        interval('2026-08-20T13:30:00.000Z', '2026-08-20T14:00:00.000Z', 'UNKNOWN'),
      ])
    })

    it('joins recorded history to projected phase at Now without changing absolute boundaries', () => {
      expect(
        composePhotoperiod(
          [
            point('2026-08-20T08:00:00.000Z', 'SUN'),
            point('2026-08-20T11:30:00.000Z', 'MOON'),
          ],
          [
            point('2026-08-20T11:45:00.000Z', 'SUN', 'projected'),
            point('2026-08-20T12:45:00.000Z', 'MOON', 'projected'),
          ],
          time('2026-08-20T13:00:00.000Z'),
          time('2026-08-20T11:45:00.000Z'),
          time('2026-08-20T13:30:00.000Z'),
          time('2026-08-20T09:00:00.000Z'),
          time('2026-08-20T14:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z')
        )
      ).toEqual([
        interval('2026-08-20T09:00:00.000Z', '2026-08-20T11:30:00.000Z', 'SUN'),
        interval('2026-08-20T11:30:00.000Z', '2026-08-20T12:00:00.000Z', 'MOON'),
        interval('2026-08-20T12:00:00.000Z', '2026-08-20T12:45:00.000Z', 'SUN'),
        interval('2026-08-20T12:45:00.000Z', '2026-08-20T13:30:00.000Z', 'MOON'),
        interval('2026-08-20T13:30:00.000Z', '2026-08-20T14:00:00.000Z', 'UNKNOWN'),
      ])
    })

    it('coalesces adjacent same-phase intervals at the recorded/projected boundary', () => {
      expect(
        composePhotoperiod(
          [point('2026-08-20T08:00:00.000Z', 'SUN')],
          [point('2026-08-20T11:45:00.000Z', 'SUN', 'projected')],
          time('2026-08-20T13:00:00.000Z'),
          time('2026-08-20T11:45:00.000Z'),
          time('2026-08-20T13:30:00.000Z'),
          time('2026-08-20T09:00:00.000Z'),
          time('2026-08-20T14:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z')
        )
      ).toEqual([
        interval('2026-08-20T09:00:00.000Z', '2026-08-20T13:30:00.000Z', 'SUN'),
        interval('2026-08-20T13:30:00.000Z', '2026-08-20T14:00:00.000Z', 'UNKNOWN'),
      ])
    })

    it('does not let an expired projection fill a recorded UNKNOWN or past gap', () => {
      expect(
        composePhotoperiod(
          [
            point('2026-08-20T08:00:00.000Z', 'MOON'),
            point('2026-08-20T10:30:00.000Z', 'UNKNOWN'),
          ],
          [point('2026-08-20T09:30:00.000Z', 'SUN', 'projected')],
          time('2026-08-20T11:00:00.000Z'),
          time('2026-08-20T08:30:00.000Z'),
          time('2026-08-20T10:00:00.000Z'),
          time('2026-08-20T09:00:00.000Z'),
          time('2026-08-20T14:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z')
        )
      ).toEqual([
        interval('2026-08-20T09:00:00.000Z', '2026-08-20T10:30:00.000Z', 'MOON'),
        interval('2026-08-20T10:30:00.000Z', '2026-08-20T14:00:00.000Z', 'UNKNOWN'),
      ])
    })

    it('does not emit a phase transition at the exclusive window end', () => {
      expect(
        composePhotoperiod(
          [
            point('2026-08-20T08:00:00.000Z', 'SUN'),
            point('2026-08-20T10:00:00.000Z', 'MOON'),
          ],
          [],
          time('2026-08-20T11:00:00.000Z'),
          Number.POSITIVE_INFINITY,
          Number.NEGATIVE_INFINITY,
          time('2026-08-20T09:00:00.000Z'),
          time('2026-08-20T10:00:00.000Z'),
          time('2026-08-20T12:00:00.000Z')
        )
      ).toEqual([interval('2026-08-20T09:00:00.000Z', '2026-08-20T10:00:00.000Z', 'SUN')])
    })
  })

})
