import { describe, expect, it } from 'vitest'

import type { ProjectionPublicationResponse } from '../../api'
import { alignSeries } from '../../data/alignSeries'
import { alignLinear } from '../../data/alignSeries.series'
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
  it('does not extend fixed windows for projected intervals', () => {
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
    const futureWidth = last - NOW.getTime()
    expect(last).toBe(NOW.getTime())
    expect(futureWidth).toBe(0)
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

  it('preserves saved rich ramps, trajectory kinds, and unavailable gaps', () => {
    const rampEnd = new Date('2026-08-02T14:00:00.000Z')
    const result = projectionTimeline({
      quality: 'estimated',
      value: [],
      trajectory: {
        contract_version: 1,
        room: 'Flower Room',
        generated_at: NOW,
        window: { start: NOW, end: rampEnd, timezone: 'UTC' },
        revision_scope: 'saved',
        base_config_revision: '8f8c3db',
        draft_revision: null,
        assumptions: [],
        warnings: [{ code: 'sample', detail: 'fixture warning' }],
        segments: [
          {
            shape: 'linear',
            start: NOW,
            end: FUTURE,
            metric: 'heating',
            unit: 'C',
            trajectory_kind: 'scheduled',
            quality: 'estimated',
            source: {
              mode: 'DAY',
              submode: null,
              period: { period_id: 'day', label: 'Day' },
              config_revision: '8f8c3db',
              draft_revision: null,
            },
            start_value: 22,
            end_value: 26,
          },
          {
            shape: 'unavailable',
            start: FUTURE,
            end: rampEnd,
            metric: 'heating',
            unit: 'C',
            trajectory_kind: 'effective',
            quality: 'unavailable',
            source: {
              mode: 'DAY',
              submode: null,
              period: { period_id: 'day', label: 'Day' },
              config_revision: '8f8c3db',
              draft_revision: null,
            },
            reason: 'not available',
          },
        ],
      },
    })

    expect(result.history?.climate).toHaveLength(2)
    const scheduled = result.history?.climate.find((series) => series.trajectory_kind === 'scheduled')
    expect(scheduled?.linear[0]?.start_value).toBe(22)
    expect(scheduled?.warnings[0]?.code).toBe('sample')
    const effective = result.history?.climate.find((series) => series.trajectory_kind === 'effective')
    expect(effective?.steps[0]?.value).toBeNull()

    const aligned = alignSeries({
      series: [],
      controlHistory: null,
      projectionHistory: result.history,
      photoperiod: [],
      live: [],
      range: { kind: 'fixed', start: NOW, end: rampEnd },
      now: NOW,
    })
    const midpoint = new Date('2026-08-02T12:15:00.000Z').getTime()
    const ramp = result.history?.climate.find((series) => series.trajectory_kind === 'scheduled')
    const normalized = ramp?.linear.map((segment) => ({
      start: segment.start.getTime(),
      end: segment.end.getTime(),
      startValue: segment.start_value,
      endValue: segment.end_value,
      origin: segment.provenance.origin,
      quality: segment.provenance.quality,
    })) ?? []
    expect(alignLinear(normalized, [midpoint], NOW.getTime(), rampEnd.getTime(), false)[0]).toBe(24)
    expect(aligned.series.some((series) => series.metric === 'heating_setpoint' && series.role === 'linear')).toBe(true)
  })

  it('ignores rich draft trajectories', () => {
    const result = projectionTimeline({
      quality: 'estimated',
      value: [],
      trajectory: {
        contract_version: 1,
        room: 'Flower Room',
        generated_at: NOW,
        window: { start: NOW, end: FUTURE, timezone: 'UTC' },
        revision_scope: 'draft',
        base_config_revision: '8f8c3db',
        draft_revision: 'draft-1',
        assumptions: [],
        warnings: [],
        segments: [],
      },
    })
    expect(result.history).toBeNull()
  })
})
