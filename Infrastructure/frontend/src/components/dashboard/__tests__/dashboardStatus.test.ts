import { describe, expect, it } from 'vitest'

import type { SensorSampleMeta } from '../../../types/sensor'
import {
  buildTrendMetric,
  deriveRoomControlContext,
  deriveRoomDecisionSummary,
  deriveClusterSensorStatus,
  type TrendData,
} from '../dashboardStatus'

const nowMs = Date.parse('2026-09-23T16:00:00.000Z')

function meta(
  source: SensorSampleMeta['source'],
  observedAtMs: number,
  invalid = false
): SensorSampleMeta {
  return { source, observedAtMs, receivedAtMs: nowMs, invalid }
}

describe('dashboard status derivations', () => {
  it('derives freshness only from the requested Flower cluster', () => {
    const splitMeta = {
      'Flower Room_front_temp': meta('poll', nowMs - 46_000),
      'Flower Room_front_rh': meta('websocket', nowMs - 60_000),
      'Flower Room_back_temp': meta('websocket', nowMs - 2_000),
    }
    const front = deriveClusterSensorStatus('Flower Room', 'front', splitMeta, nowMs)
    const back = deriveClusterSensorStatus('Flower Room', 'back', splitMeta, nowMs)

    expect(front).toEqual({
      quality: 'stale',
      newestAgeMs: 46_000,
      source: 'poll',
      cluster: 'front',
    })
    expect(back).toEqual({
      quality: 'live',
      newestAgeMs: 2_000,
      source: 'websocket',
      cluster: 'back',
    })

    const badFront = deriveClusterSensorStatus(
      'Flower Room',
      'front',
      {
        'Flower Room_front_temp': meta('poll', nowMs - 1_000, true),
        'Flower Room_back_temp': meta('websocket', nowMs - 2_000),
      },
      nowMs
    )
    expect(badFront).toEqual({
      quality: 'bad',
      newestAgeMs: null,
      source: null,
      cluster: 'front',
    })
    const backWithBadFront = deriveClusterSensorStatus(
      'Flower Room',
      'back',
      {
        'Flower Room_front_temp': meta('poll', nowMs - 1_000, true),
        'Flower Room_back_temp': meta('websocket', nowMs - 2_000),
      },
      nowMs
    )
    expect(backWithBadFront.quality).toBe('live')

    expect(
      deriveClusterSensorStatus(
        'Flower Room',
        'front',
        { 'Flower Room_back_temp': meta('websocket', nowMs - 2_000) },
        nowMs
      )
    ).toEqual({
      quality: 'missing',
      newestAgeMs: null,
      source: null,
      cluster: 'front',
    })
    expect(deriveClusterSensorStatus('Veg Room', 'main', {}, nowMs).quality).toBe('missing')
  })

  it('selects the abnormal Flower layer and keeps signed deltas', () => {
    const trend: TrendData = {
      back: {
        temperature: buildTrendMetric('temperature', [
          { timestampMs: nowMs - 60 * 60_000, value: 25.1 },
          { timestampMs: nowMs - 10 * 60_000, value: 25.2 },
          { timestampMs: nowMs, value: 25.3 },
        ]),
      },
    }
    const summary = deriveRoomDecisionSummary(
      'Flower Room',
      ['front', 'back'],
      {
        'Flower Room_front_dry_bulb_f': 23,
        'Flower Room_back_dry_bulb_b': 25,
        'Flower Room_back_vpd_b': 1.4,
        'Flower Room_main_heating_setpoint': 22,
        'Flower Room_main_cooling_setpoint': 24,
        'Flower Room_main_vpd_setpoint': 1.0,
      },
      trend,
      []
    )
    expect(summary.headline).toBe('Back TEMP HIGH')
    expect(summary.layer?.temperatureDelta).toBe(1)
    expect(summary.layer?.vpdDelta).toBeCloseTo(0.4)
    expect(summary.layer?.breachFullWindow).toBe(true)
  })

  it('keeps trend deltas consistent for ordered and out-of-order points', () => {
    const orderedPoints = [
      { timestampMs: nowMs - 20 * 60_000, value: 20 },
      { timestampMs: nowMs - 11 * 60_000, value: 21 },
      { timestampMs: nowMs - 8 * 60_000, value: 22 },
      { timestampMs: nowMs, value: 23 },
    ]
    const outOfOrderPoints = [
      orderedPoints[0],
      orderedPoints[2],
      orderedPoints[1],
      orderedPoints[3],
    ]
    const orderedBefore = orderedPoints.map(point => ({ ...point }))
    const outOfOrderBefore = outOfOrderPoints.map(point => ({ ...point }))

    const ordered = buildTrendMetric('temperature', orderedPoints)
    const outOfOrder = buildTrendMetric('temperature', outOfOrderPoints)

    expect(ordered.delta10m).toBe(2)
    expect(outOfOrder.delta10m).toBe(ordered.delta10m)
    expect(orderedPoints).toEqual(orderedBefore)
    expect(outOfOrderPoints).toEqual(outOfOrderBefore)
  })

  it('preserves breach durations and input order for ordered and out-of-order trends', () => {
    const orderedPoints = [
      { timestampMs: nowMs - 60 * 60_000, value: 23 },
      { timestampMs: nowMs - 40 * 60_000, value: 21 },
      { timestampMs: nowMs - 20 * 60_000, value: 21 },
      { timestampMs: nowMs, value: 21 },
    ]
    const outOfOrderPoints = [
      orderedPoints[3],
      orderedPoints[0],
      orderedPoints[2],
      orderedPoints[1],
    ]
    const orderedBefore = orderedPoints.map(point => ({ ...point }))
    const outOfOrderBefore = outOfOrderPoints.map(point => ({ ...point }))
    const ordered = deriveRoomDecisionSummary(
      'Veg Room',
      ['main'],
      {
        'Veg Room_main_temperature': 21,
        'Veg Room_main_heating_setpoint': 22,
        'Veg Room_main_cooling_setpoint': 24,
      },
      { main: { temperature: buildTrendMetric('temperature', orderedPoints) } },
      []
    )
    const outOfOrder = deriveRoomDecisionSummary(
      'Veg Room',
      ['main'],
      {
        'Veg Room_main_temperature': 21,
        'Veg Room_main_heating_setpoint': 22,
        'Veg Room_main_cooling_setpoint': 24,
      },
      { main: { temperature: buildTrendMetric('temperature', outOfOrderPoints) } },
      []
    )

    expect(ordered.layer?.breachMinutes).toBe(40)
    expect(ordered.layer?.breachFullWindow).toBe(false)
    expect(outOfOrder.layer).toEqual(ordered.layer)
    expect(orderedPoints).toEqual(orderedBefore)
    expect(outOfOrderPoints).toEqual(outOfOrderBefore)
  })

  it('gives failsafe precedence and suppresses mismatch while syncing', () => {
    const snapshot = {
      failsafes: [{ location: 'Veg Room', cluster: 'main' }],
      relays: [
        {
          channel: 1,
          command_mode: 'timed_on',
          command_expires_at: '2026-09-23T16:12:00.000Z',
          syncing: true,
          desired_state: 1,
          observed_state: false,
          interlock_blocked: true,
          interlock_reason: 'heater interlock',
          assignment: { location: 'Veg Room', device_name: 'heater', display_name: 'Heater' },
        },
      ],
    } as never
    const context = deriveRoomControlContext('Veg Room', snapshot, [], false)
    expect(context.mode).toBe('FAILSAFE')
    expect(context.mismatch).toBe(false)
    expect(context.syncing).toBe(true)
    expect(context.interlockReasons).toEqual(['heater interlock'])
  })
})
