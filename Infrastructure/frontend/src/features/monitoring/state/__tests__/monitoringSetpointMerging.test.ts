import { describe, expect, it } from 'vitest'
import type { ClimateTimelineSeries, ControlMonitoringResponse, TimelineStep } from '../../api'
import { mergeControlSeries } from '../../data/alignSeries.control'
import { mergeControlHistory } from '../monitoringStore.merge'

const timestamp = new Date('2026-08-20T12:00:00.000Z')
const provenance = { origin: 'recorded' as const, quality: 'exact' as const, is_aggregated: false }
const unavailableProvenance = { origin: 'recorded' as const, quality: 'unavailable' as const, is_aggregated: false }

function targetSeries(value: number | null): ClimateTimelineSeries {
  const pointProvenance = value === null ? unavailableProvenance : provenance
  const step: TimelineStep = { timestamp, value, provenance: pointProvenance }
  return {
    name: 'VPD Setpoint',
    provenance,
    projection: null,
    warnings: [],
    points: [{ timestamp, value, nominal_value: value, metric: 'vpd_setpoint', provenance: pointProvenance }],
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
    const genuineNull = mergeControlHistory(response(targetSeries(null)), response(targetSeries(null)))
    const aligned = mergeControlSeries(response({
      ...nullTarget,
      points: [...nullTarget.points, ...finiteTarget.points],
      steps: [...nullTarget.steps, ...finiteTarget.steps],
    }), null)

    // When: target timelines are deduplicated for tail/history and chart alignment.
    const merged = [nullThenFinite, finiteThenNull, genuineNull]

    // Then: finite exact targets win, while an all-null collision remains a real gap.
    expect(merged.map((item) => item.climate[0]?.points[0]?.value)).toEqual([1.2, 1.2, null])
    expect(merged.map((item) => item.climate[0]?.steps[0]?.value)).toEqual([1.2, 1.2, null])
    expect(aligned[0]?.points).toEqual([expect.objectContaining({ value: 1.2 })])
    expect(aligned[0]?.steps).toEqual([expect.objectContaining({ value: 1.2 })])
  })
})
