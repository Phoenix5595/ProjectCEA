import { describe, expect, it } from 'vitest'

import {
  applySourceFailure,
  applySourceSuccess,
  createIdleSourceOutcomes,
  deriveActiveSourceErrors,
  deriveRangeFreshness,
} from '../monitoringStore.health'

const BASELINE_SOURCES = ['sensor-history', 'control-history', 'projection'] as const
const SENSOR_SUCCESS = new Date('2026-08-02T12:00:00.000Z')
const CONTROL_SUCCESS = new Date('2026-08-02T12:01:00.000Z')
const SENSOR_FAILURE = new Date('2026-08-02T12:02:00.000Z')
const CONTROL_FAILURE = new Date('2026-08-02T12:03:00.000Z')

describe('monitoring source outcomes', () => {
  it('baseline characterization keeps the three independent range sources', () => {
    expect(BASELINE_SOURCES).toEqual(['sensor-history', 'control-history', 'projection'])
  })

  it('clears only the source that succeeds', () => {
    const controlFailed = applySourceFailure(createIdleSourceOutcomes(), {
      source: 'control-history',
      message: 'range unavailable',
      errorAt: CONTROL_FAILURE,
    })
    const controlAndProjectionFailed = applySourceFailure(controlFailed, {
      source: 'projection',
      message: 'range unavailable',
      errorAt: CONTROL_FAILURE,
    })
    const sensorAndControlFailed = applySourceFailure(controlAndProjectionFailed, {
      source: 'sensor-history',
      message: 'range unavailable',
      errorAt: SENSOR_FAILURE,
    })
    const sensorRecovered = applySourceSuccess(sensorAndControlFailed, {
      source: 'sensor-history',
      lastGoodAt: SENSOR_SUCCESS,
    })

    expect(deriveActiveSourceErrors(sensorRecovered)).toEqual([
      {
        source: 'control-history',
        message: 'range unavailable',
        errorAt: CONTROL_FAILURE,
      },
      {
        source: 'projection',
        message: 'range unavailable',
        errorAt: CONTROL_FAILURE,
      },
    ])
    expect(deriveActiveSourceErrors(sensorAndControlFailed)).toHaveLength(3)
  })

  it('returns independent frozen outcomes without mutating prior state', () => {
    const idle = createIdleSourceOutcomes()
    const failed = applySourceFailure(idle, {
      source: 'sensor-history',
      message: 'range unavailable',
      errorAt: SENSOR_FAILURE,
    })

    expect(failed).not.toBe(idle)
    expect(idle['sensor-history'].status).toBe('idle')
    expect(failed['sensor-history'].status).toBe('failed')
    expect(Object.isFrozen(failed)).toBe(true)
    expect(Object.isFrozen(failed['sensor-history'])).toBe(true)
  })

  it('keeps identical failure messages distinct by source', () => {
    const outcomes = applySourceFailure(
      applySourceFailure(createIdleSourceOutcomes(), {
        source: 'sensor-history',
        message: 'request failed',
        errorAt: SENSOR_FAILURE,
      }),
      {
        source: 'projection',
        message: 'request failed',
        errorAt: CONTROL_FAILURE,
      },
    )

    expect(deriveActiveSourceErrors(outcomes)).toEqual([
      { source: 'sensor-history', message: 'request failed', errorAt: SENSOR_FAILURE },
      { source: 'projection', message: 'request failed', errorAt: CONTROL_FAILURE },
    ])
  })

  it('uses the conservative minimum successful history timestamp', () => {
    const outcomes = applySourceSuccess(
      applySourceSuccess(createIdleSourceOutcomes(), {
        source: 'sensor-history',
        lastGoodAt: SENSOR_SUCCESS,
      }),
      {
        source: 'control-history',
        lastGoodAt: CONTROL_SUCCESS,
      },
    )

    expect(deriveRangeFreshness(outcomes)).toEqual({
      lastGoodRangeAt: SENSOR_SUCCESS,
      rangeErrorAt: null,
    })
  })

  it('does not let projection failure mark the recorded range stale', () => {
    const outcomes = applySourceFailure(
      applySourceSuccess(
        applySourceSuccess(createIdleSourceOutcomes(), {
          source: 'sensor-history',
          lastGoodAt: SENSOR_SUCCESS,
        }),
        {
          source: 'control-history',
          lastGoodAt: CONTROL_SUCCESS,
        },
      ),
      {
        source: 'projection',
        message: 'projection unavailable',
        errorAt: CONTROL_FAILURE,
      },
    )

    expect(deriveActiveSourceErrors(outcomes)).toEqual([
      {
        source: 'projection',
        message: 'projection unavailable',
        errorAt: CONTROL_FAILURE,
      },
    ])
    expect(deriveRangeFreshness(outcomes)).toEqual({
      lastGoodRangeAt: SENSOR_SUCCESS,
      rangeErrorAt: null,
    })
  })

  it('uses the latest active sensor or control failure timestamp', () => {
    const outcomes = applySourceFailure(
      applySourceFailure(
        applySourceSuccess(
          applySourceSuccess(createIdleSourceOutcomes(), {
            source: 'sensor-history',
            lastGoodAt: SENSOR_SUCCESS,
          }),
          {
            source: 'control-history',
            lastGoodAt: CONTROL_SUCCESS,
          },
        ),
        {
          source: 'sensor-history',
          message: 'sensor unavailable',
          errorAt: SENSOR_FAILURE,
        },
      ),
      {
        source: 'control-history',
        message: 'control unavailable',
        errorAt: CONTROL_FAILURE,
      },
    )

    expect(deriveRangeFreshness(outcomes)).toEqual({
      lastGoodRangeAt: SENSOR_SUCCESS,
      rangeErrorAt: CONTROL_FAILURE,
    })
  })
})
