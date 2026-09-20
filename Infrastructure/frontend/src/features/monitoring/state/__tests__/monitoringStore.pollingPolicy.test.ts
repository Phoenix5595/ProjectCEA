import { describe, expect, it } from 'vitest'

import type { FixedRange, LiveRange } from '../monitoringStore.types'
import {
  controlTailStart,
  isSourceRetryEligible,
  pollingEligibility,
} from '../monitoringStore.pollingPolicy'

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
})
