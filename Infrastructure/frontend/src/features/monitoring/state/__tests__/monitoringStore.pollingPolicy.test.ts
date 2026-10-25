import { describe, expect, it } from 'vitest'

import {
  controlTailStart,
  fixedRangeRetryDelay,
  isSourceRetryEligible,
  nextSensorIncrementalAt,
  pollingEligibility,
  sensorIncrementalStart,
} from '../monitoringStore.pollingPolicy'
import type { FixedRange, LiveRange } from '../monitoringStore.types'

const NOW = new Date('2026-09-12T12:00:00.000Z')
const TWO_HOURS_MS = 2 * 60 * 60 * 1000

const liveRange: LiveRange = { kind: 'live', duration: 60 * 60 * 1000 }
const fixedRange: FixedRange = {
  kind: 'fixed',
  start: new Date('2026-09-12T09:00:00.000Z'),
  end: new Date('2026-09-12T10:00:00.000Z'),
}

describe('monitoring polling policy', () => {
  it('allows current values in fixed mode while disabling recorded work', () => {
    const eligibility = pollingEligibility(fixedRange)

    expect(eligibility).toEqual({
      currentValues: true,
      sensorHistory: false,
      controlTail: false,
      projection: false,
    })
  })

  it('allows current values and recorded work in live mode', () => {
    const eligibility = pollingEligibility(liveRange)

    expect(eligibility).toEqual({
      currentValues: true,
      sensorHistory: true,
      controlTail: true,
      projection: true,
    })
  })

  it('clamps a stale control anchor to the two-minute tail', () => {
    const staleLast = new Date(NOW.getTime() - 3 * 60 * 60 * 1000)

    const start = controlTailStart(NOW, staleLast)

    expect(start.getTime()).toBe(NOW.getTime() - 120_000)
    expect(NOW.getTime() - start.getTime()).toBeLessThan(TWO_HOURS_MS)
  })

  it('retains a two-second overlap for a recent control anchor', () => {
    const recentLast = new Date(NOW.getTime() - 10_000)

    const start = controlTailStart(NOW, recentLast)

    expect(start.getTime()).toBe(recentLast.getTime() - 2_000)
  })

  it('clamps a control anchor ahead of the local clock below the end', () => {
    const skewedLast = new Date(NOW.getTime() + 30_000)

    const start = controlTailStart(NOW, skewedLast)

    expect(start.getTime()).toBeLessThan(NOW.getTime())
  })

  it.each([
    ['sensor-history', 60_000],
    ['control-history', 30_000],
    ['projection', 30_000],
  ] as const)('keeps %s retry ineligible before its cadence', (source, cadenceMs) => {
    const lastAttemptAt = new Date(NOW.getTime() - cadenceMs + 1)

    expect(isSourceRetryEligible(source, NOW, lastAttemptAt)).toBe(false)
  })

  it.each([
    ['sensor-history', 60_000],
    ['control-history', 30_000],
    ['projection', 30_000],
  ] as const)('makes %s retry eligible strictly after its cadence', (source, cadenceMs) => {
    const lastAttemptAt = new Date(NOW.getTime() - cadenceMs - 1)

    expect(isSourceRetryEligible(source, NOW, lastAttemptAt)).toBe(true)
  })

  it('makes a source with no previous attempt immediately eligible', () => {
    expect(isSourceRetryEligible('sensor-history', NOW, null)).toBe(true)
  })

  it('disables every recorded and live source for a historical store', () => {
    expect(pollingEligibility(liveRange, 'historical')).toEqual({
      currentValues: false,
      sensorHistory: false,
      controlTail: false,
      projection: false,
    })
  })

  it('retries a failed fixed range at 5 s, 15 s, then every 60 s', () => {
    expect(fixedRangeRetryDelay(0)).toBe(0)
    expect(fixedRangeRetryDelay(1)).toBe(5_000)
    expect(fixedRangeRetryDelay(2)).toBe(15_000)
    expect(fixedRangeRetryDelay(3)).toBe(60_000)
    expect(fixedRangeRetryDelay(9)).toBe(60_000)
  })

  it('waits for the 5 s minimum or the next completed bucket, whichever is later', () => {
    const lastRefreshAt = NOW.getTime()
    const bucketCompleteUntil = NOW.getTime() + 2_000

    expect(nextSensorIncrementalAt(lastRefreshAt, bucketCompleteUntil, 60_000)).toBe(
      NOW.getTime() + 65_000
    )
    expect(nextSensorIncrementalAt(lastRefreshAt, NOW.getTime() - 60_000, 1_000)).toBe(
      NOW.getTime() + 5_000
    )
  })

  it('overlaps the incremental start by two buckets aligned down to the interval', () => {
    const baseline = Date.parse('2026-09-12T12:05:00.000Z')

    expect(sensorIncrementalStart(baseline, 60_000)).toBe(
      Date.parse('2026-09-12T12:03:00.000Z')
    )
    expect(sensorIncrementalStart(baseline + 30_000, 600_000)).toBe(
      Date.parse('2026-09-12T11:40:00.000Z')
    )
  })
})
