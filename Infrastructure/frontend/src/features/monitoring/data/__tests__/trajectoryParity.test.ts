import { describe, expect, it } from 'vitest'

import type { ClimateTimelineSeries, ControlMonitoringResponse, Origin, Quality } from '../../api'
import { alignSeries } from '../alignSeries'
import type { AlignInput } from '../alignSeries.types'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const END = new Date('2026-08-02T13:00:00.000Z')

function provenance(origin: Origin, quality: Quality) {
  return { origin, quality, is_aggregated: false }
}

function climateSeries(
  points: ClimateTimelineSeries['points'],
  linear: ClimateTimelineSeries['linear'] = []
): ClimateTimelineSeries {
  return {
    name: 'Heating Setpoint',
    metric: 'heating_setpoint',
    trajectory_kind: 'scheduled',
    provenance: provenance('recorded', 'exact'),
    projection: null,
    warnings: [],
    points,
    steps: [],
    linear,
  }
}

function controlResponse(climate: ClimateTimelineSeries[]): ControlMonitoringResponse {
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
  }
}

function point(time: Date, value: number, origin: Origin, quality: Quality) {
  return {
    time,
    value,
    nominal_value: value,
    metric: 'heating_setpoint',
    provenance: provenance(origin, quality),
  }
}

describe('trajectory parity', () => {
  it('continues recorded history into the projected ramp as one line', () => {
    const history = controlResponse([
      climateSeries([point(START, 22, 'recorded', 'exact'), point(NOW, 23, 'recorded', 'exact')]),
    ])
    const projection = controlResponse([
      {
        ...climateSeries(
          [],
          [
            {
              start: NOW,
              end: END,
              start_value: 23,
              end_value: 25,
              provenance: provenance('projected', 'estimated'),
            },
          ]
        ),
        provenance: provenance('projected', 'estimated'),
      },
    ])
    const input: AlignInput = {
      series: [],
      controlHistory: history,
      projectionHistory: projection,
      photoperiod: [],
      live: [],
      range: { kind: 'fixed', start: START, end: END },
      now: NOW,
      maxPoints: 100,
    }

    const aligned = alignSeries(input)
    const heating = aligned.series.filter(series => series.metric === 'heating_setpoint')
    // One line per target: the ramp replaces the held value where it applies.
    expect(heating.map(series => series.role)).toEqual(['step'])
    const line = heating[0]
    const startIndex = aligned.x.indexOf(START.getTime())
    const nowIndex = aligned.x.indexOf(NOW.getTime())
    const endIndex = aligned.x.indexOf(END.getTime())

    expect(line?.y[startIndex]).toBe(22)
    expect(line?.y[nowIndex]).toBe(23)
    expect(line?.y[endIndex]).toBe(25)
  })
})
