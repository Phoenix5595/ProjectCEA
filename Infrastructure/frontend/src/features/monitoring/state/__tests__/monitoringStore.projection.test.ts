import { describe, expect, it } from 'vitest'

import type { ProjectionPublicationResponse } from '../../api'
import { alignSeries } from '../../data/alignSeries'
import { projectionTimeline } from '../monitoringStore.projection'

const START = new Date('2026-08-02T11:00:00.000Z')
const NOW = new Date('2026-08-02T12:00:00.000Z')
const FUTURE = new Date('2026-08-02T12:30:00.000Z')

function publication(): ProjectionPublicationResponse {
  return {
    quality: 'estimated',
    value: [
      {
        version: { contract_version: 1, config_version: 7, revision: '8f8c3db' },
        generated_at: NOW,
        valid_from: NOW,
        valid_until: FUTURE,
        series: [
          {
            series_id: { value: 'climate.heating_setpoint_target' },
            value: 22,
            quality: 'estimated',
            valid_from: NOW,
            valid_until: FUTURE,
          },
        ],
      },
    ],
  }
}

describe('projectionTimeline', () => {
  it('renders capped future intervals without requiring recorded values', () => {
    const history = projectionTimeline(publication()).history
    expect(history).not.toBeNull()
    if (history === null) return

    const data = alignSeries({
      series: [],
      controlHistory: null,
      projectionHistory: history,
      photoperiod: [],
      live: [],
      range: { kind: 'fixed', start: START, end: NOW },
      now: NOW,
    })

    const point = data.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'step')
    expect(point?.y[data.x.indexOf(NOW.getTime())]).toBe(22)

    const last = data.x[data.x.length - 1]
    const recordedWidth = NOW.getTime() - START.getTime()
    const futureWidth = last - NOW.getTime()
    expect(last).toBeLessThan(FUTURE.getTime())
    expect(futureWidth).toBeGreaterThan(0)
    expect(futureWidth).toBeLessThanOrEqual(recordedWidth / 9 + Number.EPSILON)
  })

  it('keeps unavailable publications out of chart timelines', () => {
    const result = projectionTimeline({ quality: 'unavailable', value: [] })

    expect(result.history).toBeNull()
    expect(result.revision).toBeNull()
    expect(result.validUntil).toBeNull()
  })

  it('keeps a recorded step over an overlapping projected step', () => {
    const projected = projectionTimeline(publication()).history
    expect(projected).not.toBeNull()
    if (projected === null) return
    const recorded = {
      ...projected,
      climate: projected.climate.map((series) => ({
        ...series,
        provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
        steps: [{
          timestamp: NOW,
          value: 99,
          provenance: { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false },
        }],
      })),
    }

    const data = alignSeries({
      series: [],
      controlHistory: recorded,
      projectionHistory: projected,
      photoperiod: [],
      live: [],
      range: { kind: 'fixed', start: START, end: NOW },
      now: NOW,
    })

    const step = data.series.find((series) => series.metric === 'heating_setpoint' && series.role === 'step')
    expect(step?.y[data.x.indexOf(NOW.getTime())]).toBe(99)
    expect(data.series.filter((series) => series.metric === 'heating_setpoint' && series.role === 'step')).toHaveLength(1)
  })
})
