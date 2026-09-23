import { describe, expect, it } from 'vitest'

import type { SensorSampleMeta } from '../../../types/sensor'
import {
  buildTrendMetric,
  deriveRoomControlContext,
  deriveRoomDecisionSummary,
  deriveRoomSensorStatus,
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
  it('uses bad, stale, missing, live quality precedence', () => {
    const bad = deriveRoomSensorStatus(
      'Flower Room',
      ['front', 'back'],
      {
        'Flower Room_front_temp': meta('poll', nowMs - 1_000),
        'Flower Room_back_temp': meta('websocket', nowMs - 1_000, true),
      },
      nowMs
    )
    expect(bad.quality).toBe('bad')

    const stale = deriveRoomSensorStatus(
      'Flower Room',
      ['front'],
      { 'Flower Room_front_temp': meta('poll', nowMs - 46_000) },
      nowMs
    )
    expect(stale.quality).toBe('stale')
    expect(deriveRoomSensorStatus('Veg Room', ['main'], {}, nowMs).quality).toBe('missing')
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
