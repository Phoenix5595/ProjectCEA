import { describe, expect, it } from 'vitest'

import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  LightTimelineSeries,
  TimelineStep,
} from '../../api'
import { mergeControlSeries } from '../../data/alignSeries.control'
import { mergeControlHistory } from '../monitoringStore.merge'

const timestamp = new Date('2026-08-20T12:00:00.000Z')
const provenance = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false }
const unavailableProvenance = {
  origin: 'recorded' as const,
  quality: 'unavailable' as const,
  is_aggregated: false,
}

function targetSeries(value: number | null): ClimateTimelineSeries {
  const pointProvenance = value === null ? unavailableProvenance : provenance
  const step: TimelineStep = { timestamp, value, provenance: pointProvenance }
  return {
    name: 'VPD Setpoint',
    provenance,
    projection: null,
    warnings: [],
    points: [
      {
        timestamp,
        value,
        nominal_value: value,
        metric: 'vpd_setpoint',
        provenance: pointProvenance,
      },
    ],
    steps: [step],
    linear: [],
  }
}

function response(series: ClimateTimelineSeries): ControlMonitoringResponse {
  return {
    range: { start: timestamp, end: new Date(timestamp.getTime() + 60_000) },
    runtime_snapshot_version: 1,
    cursors: [],
    flush_health: [],
    climate: [series],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

describe('setpoint target merging', () => {
  it('keeps finite same-timestamp target points and steps over nulls in either order', () => {
    // Given: tail/history overlaps that contain a null and an exact VPD target in both orders.
    const nullTarget = targetSeries(null)
    const finiteTarget = targetSeries(1.2)
    const nullThenFinite = mergeControlHistory(response(nullTarget), response(finiteTarget))
    const finiteThenNull = mergeControlHistory(response(finiteTarget), response(nullTarget))
    const genuineNull = mergeControlHistory(
      response(targetSeries(null)),
      response(targetSeries(null))
    )
    const aligned = mergeControlSeries(
      response({
        ...nullTarget,
        points: [...nullTarget.points, ...finiteTarget.points],
        steps: [...nullTarget.steps, ...finiteTarget.steps],
      }),
      null
    )

    // When: target timelines are deduplicated for tail/history and chart alignment.
    const merged = [nullThenFinite, finiteThenNull, genuineNull]

    // Then: finite exact targets win, while an all-null collision remains a real gap.
    expect(merged.map(item => item.climate[0]?.points[0]?.value)).toEqual([1.2, 1.2, null])
    expect(merged.map(item => item.climate[0]?.steps[0]?.value)).toEqual([1.2, 1.2, null])
    expect(aligned[0]?.points).toEqual([expect.objectContaining({ value: 1.2 })])
    expect(aligned[0]?.steps).toEqual([expect.objectContaining({ value: 1.2 })])
  })
})

describe('light history merging', () => {
  it('merges canonical identity and sorts points, steps, and ramp intervals', () => {
    const first = new Date(0)
    const second = new Date(10)
    const third = new Date(20)
    const fourth = new Date(30)
    const light = (
      name: string,
      metric: string,
      points: LightTimelineSeries['points'],
      steps: LightTimelineSeries['steps'],
      linear: LightTimelineSeries['linear']
    ): LightTimelineSeries => ({
      name,
      metric,
      trajectory_kind: 'effective',
      provenance,
      warnings: [],
      points,
      steps,
      linear,
    })
    const recorded = light(
      'light_f_1',
      'light_f_1',
      [
        {
          timestamp: first,
          value: 40,
          nominal_value: 40,
          device_name: 'light_f_1',
          provenance,
        },
      ],
      [{ timestamp: first, value: 40, provenance }],
      [
        {
          start: third,
          end: fourth,
          start_value: 60,
          end_value: 80,
          provenance,
        },
      ]
    )
    const tail = light(
      'Effective projection',
      'light.intensity.light_f_1',
      [
        {
          timestamp: second,
          value: 50,
          nominal_value: 50,
          device_name: 'light_f_1',
          provenance,
        },
      ],
      [{ timestamp: second, value: 50, provenance }],
      [
        {
          start: first,
          end: second,
          start_value: 30,
          end_value: 50,
          provenance,
        },
      ]
    )

    const budgetedTail = light(
      'Budgeted label',
      'light.intensity.light_f_1',
      [],
      [{ timestamp: third, value: 60, provenance }],
      [
        {
          start: second,
          end: third,
          start_value: 50,
          end_value: 60,
          provenance,
        },
      ]
    )
    const existing = response(targetSeries(1.2))
    const incoming = response(targetSeries(1.2))
    existing.lights = [recorded]
    incoming.lights = [tail, budgetedTail]

    const merged = mergeControlHistory(existing, incoming)

    expect(merged.lights).toHaveLength(1)
    expect(merged.lights[0]?.name).toBe('light_f_1')
    expect(merged.lights[0]?.metric).toBe('light_f_1')
    expect(merged.lights[0]?.points.map(point => point.timestamp)).toEqual([first, second])
    expect(merged.lights[0]?.steps.map(step => step.timestamp)).toEqual([first, second, third])
    expect(merged.lights[0]?.linear.map(segment => [segment.start, segment.end])).toEqual([
      [first, second],
      [second, third],
      [third, fourth],
    ])
  })
})
