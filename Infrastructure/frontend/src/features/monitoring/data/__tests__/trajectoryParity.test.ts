import { describe, expect, it } from 'vitest'

import type {
  ClimateTimelineSeries,
  ControlMonitoringResponse,
  Origin,
  Quality,
} from '../../api'
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
  linear: ClimateTimelineSeries['linear'] = [],
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

function point(timestamp: Date, value: number, origin: Origin, quality: Quality) {
  return {
    timestamp,
    value,
    nominal_value: value,
    metric: 'heating_setpoint',
    provenance: provenance(origin, quality),
  }
}

describe('trajectory parity', () => {
  it('continues recorded scheduled history into the projected ramp', () => {
    const history = controlResponse([
      climateSeries([
        point(START, 22, 'recorded', 'exact'),
        point(NOW, 23, 'recorded', 'exact'),
      ]),
    ])
    const projection = controlResponse([
      {
        ...climateSeries([], [
          {
            start: NOW,
            end: END,
            start_value: 23,
            end_value: 25,
            provenance: provenance('projected', 'estimated'),
          },
        ]),
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
    const step = aligned.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'step')
    const ramp = aligned.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'linear')
    const startIndex = aligned.x.indexOf(START.getTime())
    const nowIndex = aligned.x.indexOf(NOW.getTime())
    const endIndex = aligned.x.indexOf(END.getTime())

    expect(step?.y[startIndex]).toBe(22)
    expect(step?.y[nowIndex]).toBe(23)
    expect(step?.y[endIndex]).toBe(23)
    expect(ramp?.y[nowIndex]).toBe(23)
    expect(ramp?.y[endIndex]).toBe(25)
  })
})
