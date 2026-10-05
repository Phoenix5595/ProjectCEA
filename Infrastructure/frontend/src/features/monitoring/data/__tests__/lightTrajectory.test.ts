import { describe, expect, it } from 'vitest'

import type { ControlMonitoringResponse, LightTimelineSeries } from '../../api'
import type { NormControlSeries, NormLinear, NormPoint, NormStep } from '../alignSeries.types'
import { canonicalLightDeviceName, mergeLightSeries } from '../alignSeries.control'
import { composeLightTrajectory, lightSegmentAt, lightValueAt } from '../lightTrajectory'

function normalized(
  overrides: Partial<NormControlSeries> = {}
): NormControlSeries {
  return {
    name: 'light_f_1',
    metric: 'light_f_1',
    kind: 'light',
    trajectoryKind: null,
    points: [],
    steps: [],
    linear: [],
    seriesOrigin: 'recorded',
    seriesQuality: 'exact',
    seriesIsAggregated: false,
    ...overrides,
  }
}

function point(
  t: number,
  value: number | null,
  origin: NormPoint['origin'] = 'recorded',
  quality: NormPoint['quality'] = 'exact'
): NormPoint {
  return { t, value, origin, quality, isAggregated: false }
}

function step(
  t: number,
  value: number | null,
  origin: NormStep['origin'] = 'projected',
  quality: NormStep['quality'] = value === null ? 'unavailable' : 'estimated'
): NormStep {
  return { t, value, origin, quality }
}

function ramp(
  start: number,
  end: number,
  startValue: number,
  endValue: number,
  origin: NormLinear['origin'] = 'projected',
  quality: NormLinear['quality'] = 'estimated'
): NormLinear {
  return { start, end, startValue, endValue, origin, quality }
}

function response(lights: LightTimelineSeries[]): ControlMonitoringResponse {
  return {
    range: { start: new Date(0), end: new Date(60) },
    runtime_snapshot_version: 0,
    cursors: [],
    flush_health: [],
    climate: [],
    lights,
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

function projectedLight(
  name: string,
  metric: string,
  trajectoryKind: 'scheduled' | 'effective' | null,
  steps: LightTimelineSeries['steps']
): LightTimelineSeries {
  return {
    name,
    metric,
    trajectory_kind: trajectoryKind,
    provenance: { origin: 'projected', quality: 'estimated', is_aggregated: false },
    warnings: [],
    points: [],
    steps,
    linear: [],
  }
}

describe('light trajectory composition', () => {
  it('composes recorded coverage, projected steps, a ramp, and an unavailable gap', () => {
    const history = normalized({ points: [point(0, 40)] })
    const projection = normalized({
      seriesOrigin: 'projected',
      seriesQuality: 'estimated',
      steps: [step(10, 40), step(40, null), step(50, 0)],
      linear: [ramp(20, 30, 40, 80)],
    })

    const trajectory = composeLightTrajectory(history, projection, 10, 60)

    expect(
      trajectory.map(({ start, end, shape }) => ({ start, end, shape }))
    ).toEqual([
      { start: 0, end: 10, shape: 'step' },
      { start: 10, end: 20, shape: 'step' },
      { start: 20, end: 30, shape: 'linear' },
      { start: 30, end: 40, shape: 'step' },
      { start: 40, end: 50, shape: 'step' },
      { start: 50, end: 60, shape: 'step' },
    ])
    expect([15, 25, 35, 45, 55, 60].map(time => lightValueAt(trajectory, time))).toEqual([
      40,
      60,
      80,
      null,
      0,
      null,
    ])
    expect(lightSegmentAt(trajectory, 30)?.startValue).toBe(80)
    expect(lightSegmentAt(trajectory, 60)).toBeUndefined()
  })

  it('lets recorded samples override projected values only through recorded coverage', () => {
    const history = normalized({
      points: [point(0, 40), point(10, 35)],
    })
    const projection = normalized({
      seriesOrigin: 'projected',
      seriesQuality: 'estimated',
      steps: [step(10, 40)],
    })

    const trajectory = composeLightTrajectory(history, projection, 15, 60)

    expect([10, 12, 15].map(time => lightValueAt(trajectory, time))).toEqual([35, 35, 40])
    expect(lightSegmentAt(trajectory, 12)?.origin).toBe('recorded')
    expect(lightSegmentAt(trajectory, 15)?.origin).toBe('projected')
  })

  it('keeps explicit recorded nulls unavailable and does not extend history past its end', () => {
    const history = normalized({ points: [point(0, 40), point(10, null)] })
    const projection = normalized({
      seriesOrigin: 'projected',
      seriesQuality: 'estimated',
      steps: [step(10, 75)],
    })

    const trajectory = composeLightTrajectory(history, projection, 15, 30)

    expect([10, 14, 15, 29, 30].map(time => lightValueAt(trajectory, time))).toEqual([
      null,
      null,
      75,
      75,
      null,
    ])
  })

  it('does not extend a recorded hold beyond its response without a projection', () => {
    const history = normalized({ points: [point(0, 40)] })
    const trajectory = composeLightTrajectory(history, undefined, 10, 0)

    expect(lightValueAt(trajectory, 9)).toBe(40)
    expect(lightValueAt(trajectory, 10)).toBeNull()
    expect(mergeLightSeries(null, null)).toEqual([])
  })

  it('lets an explicit unavailable step win at a ramp endpoint', () => {
    const projection = normalized({
      seriesOrigin: 'projected',
      seriesQuality: 'estimated',
      steps: [step(10, null)],
      linear: [ramp(0, 10, 20, 80)],
    })

    const trajectory = composeLightTrajectory(undefined, projection, 0, 20)

    expect(lightValueAt(trajectory, 9)).toBe(74)
    expect(lightValueAt(trajectory, 10)).toBeNull()
    expect(lightSegmentAt(trajectory, 10)?.quality).toBe('unavailable')
  })

  it('turns a zero-length ramp into its end-value step without division', () => {
    const projection = normalized({
      seriesOrigin: 'projected',
      seriesQuality: 'estimated',
      linear: [ramp(10, 10, 20, 60)],
    })

    const trajectory = composeLightTrajectory(undefined, projection, 0, 20)

    expect(trajectory).toMatchObject([
      { start: 10, end: 20, shape: 'step', startValue: 60, endValue: 60 },
    ])
    expect(lightValueAt(trajectory, 10)).toBe(60)
    expect(lightValueAt(trajectory, 20)).toBeNull()
    expect(composeLightTrajectory(undefined, undefined, 20, 20)).toEqual([])
  })
})

describe('light trajectory identity merging', () => {
  it('uses point device identity and selects unavailable effective projection over scheduled', () => {
    const provenance = { origin: 'projected' as const, quality: 'estimated' as const, is_aggregated: false }
    const unavailable = projectedLight('Effective projection', 'light.intensity.light_f_1', 'effective', [
      {
        timestamp: new Date(10),
        value: null,
        provenance: { ...provenance, quality: 'unavailable' },
      },
    ])
    const scheduled = projectedLight(
      'Scheduled projection',
      'light.intensity.light_f_1',
      'scheduled',
      [{ timestamp: new Date(10), value: 80, provenance }]
    )
    const pointIdentity: LightTimelineSeries = {
      ...projectedLight('Presentation label is not identity', 'wrong_metric', null, []),
      points: [
        {
          timestamp: new Date(0),
          value: 40,
          nominal_value: 40,
          device_name: 'light_f_1',
          provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
        },
      ],
    }
    const bareMetric = projectedLight('Light V 1', 'light_v_1', null, [
      { timestamp: new Date(10), value: 20, provenance },
    ])

    const merged = mergeLightSeries(
      response([pointIdentity]),
      response([scheduled, unavailable, bareMetric])
    )

    expect(merged.map(light => light.deviceName)).toEqual(['light_f_1', 'light_v_1'])
    expect(merged[0]?.history?.metric).toBe('light_f_1')
    expect(merged[0]?.projection?.trajectoryKind).toBe('effective')
    expect(merged[0]?.projection?.steps[0]?.value).toBeNull()
    expect(merged[1]?.projection?.metric).toBe('light_v_1')
    expect(canonicalLightDeviceName('light.intensity.light_f_1')).toBe('light_f_1')
    expect(canonicalLightDeviceName('light_f_1')).toBe('light_f_1')
  })

  it('merges repeated same-kind observations with finite facts preferred to nulls', () => {
    const unavailable = projectedLight('Light light_f_1', 'light_f_1', 'effective', [
      {
        timestamp: new Date(10),
        value: null,
        provenance: { origin: 'recorded', quality: 'unavailable', is_aggregated: false },
      },
    ])
    const finite = projectedLight('Light light_f_1', 'light_f_1', 'effective', [
      {
        timestamp: new Date(10),
        value: 45,
        provenance: { origin: 'recorded', quality: 'exact', is_aggregated: false },
      },
    ])

    const merged = mergeLightSeries(response([unavailable, finite]), null)

    expect(merged).toHaveLength(1)
    expect(merged[0]?.history?.steps).toMatchObject([
      { t: 10, value: 45, origin: 'recorded', quality: 'exact' },
    ])
  })
})
