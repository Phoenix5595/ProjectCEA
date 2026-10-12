/**
 * Tests for the origin-aware historical drill-down routing.
 *
 * The table covers both stages: from an overview longer than seven days (long
 * selections keep their span, short ones snap to exactly seven days centred
 * and clamped) and from the detailed seven-day reader (sub-seven-day
 * selections hand the exact range to regular Monitoring, floored to the
 * toolbar minimum).
 */
import { describe, expect, it } from 'vitest'

import { MAX_RANGE_MS, MIN_RANGE_MS } from '../../monitoring/components/timeRangeToolbar.time'
import type { FixedRange } from '../../monitoring/state'
import { resolveHistoricalSelection } from '../historicalSelection'

function fixed(startIso: string, endIso: string): FixedRange {
  return { kind: 'fixed', start: new Date(startIso), end: new Date(endIso) }
}

const OVERVIEW = fixed('2026-07-01T00:00:00.000Z', '2026-08-01T00:00:00.000Z')
const SEVEN_DAY_DETAIL = fixed('2026-07-15T10:00:00.000Z', '2026-07-22T10:00:00.000Z')

describe('resolveHistoricalSelection', () => {
  it('keeps an at-least-seven-day overview selection with its actual span', () => {
    const outcome = resolveHistoricalSelection(
      OVERVIEW,
      new Date('2026-07-02T00:00:00.000Z'),
      new Date('2026-07-25T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('history')
    expect(outcome.start.toISOString()).toBe('2026-07-02T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-25T00:00:00.000Z')
  })

  it('snaps a sub-seven-day overview selection to exactly seven days centred on it', () => {
    const outcome = resolveHistoricalSelection(
      OVERVIEW,
      new Date('2026-07-23T00:00:00.000Z'),
      new Date('2026-07-26T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('history')
    expect(outcome.start.toISOString()).toBe('2026-07-21T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-28T00:00:00.000Z')
    expect(outcome.end.getTime() - outcome.start.getTime()).toBe(MAX_RANGE_MS)
  })

  it('clamps the snap forward when the centred window leaves the overview start', () => {
    const outcome = resolveHistoricalSelection(
      OVERVIEW,
      new Date('2026-07-02T00:00:00.000Z'),
      new Date('2026-07-03T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('history')
    expect(outcome.start.toISOString()).toBe('2026-07-01T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-08T00:00:00.000Z')
  })

  it('clamps the snap backward when the centred window leaves the overview end', () => {
    const outcome = resolveHistoricalSelection(
      OVERVIEW,
      new Date('2026-07-29T00:00:00.000Z'),
      new Date('2026-07-30T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('history')
    expect(outcome.start.toISOString()).toBe('2026-07-25T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-08-01T00:00:00.000Z')
  })

  it('hands sub-seven-day selections, including five to seven days, from the seven-day reader to regular Monitoring', () => {
    const outcome = resolveHistoricalSelection(
      SEVEN_DAY_DETAIL,
      new Date('2026-07-16T00:00:00.000Z'),
      new Date('2026-07-22T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('monitoring')
    expect(outcome.start.toISOString()).toBe('2026-07-16T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-22T00:00:00.000Z')
    expect(outcome.end.getTime() - outcome.start.getTime()).toBe(6 * 24 * 3600_000)
  })

  it('floors a zero-width selection to the five-minute toolbar minimum', () => {
    const selection = new Date('2026-07-17T00:00:00.000Z')
    const outcome = resolveHistoricalSelection(SEVEN_DAY_DETAIL, selection, selection)

    expect(outcome.surface).toBe('monitoring')
    expect(outcome.start.toISOString()).toBe('2026-07-17T00:00:00.000Z')
    expect(outcome.end.getTime() - outcome.start.getTime()).toBe(MIN_RANGE_MS)
  })

  it('keeps a full seven-day window selection from the seven-day reader on Historical', () => {
    const outcome = resolveHistoricalSelection(
      SEVEN_DAY_DETAIL,
      SEVEN_DAY_DETAIL.start,
      SEVEN_DAY_DETAIL.end
    )

    expect(outcome.surface).toBe('history')
    expect(outcome.start.toISOString()).toBe('2026-07-15T10:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-22T10:00:00.000Z')
  })

  it('treats a six-day detailed reader as the detailed stage too', () => {
    const outcome = resolveHistoricalSelection(
      fixed('2026-07-16T10:00:00.000Z', '2026-07-22T10:00:00.000Z'),
      new Date('2026-07-17T00:00:00.000Z'),
      new Date('2026-07-22T00:00:00.000Z')
    )

    expect(outcome.surface).toBe('monitoring')
    expect(outcome.end.getTime() - outcome.start.getTime()).toBe(5 * 24 * 3600_000)
  })

  it('treats a live displayed range as the detailed stage', () => {
    const outcome = resolveHistoricalSelection(
      { kind: 'live', duration: 3 * 3600_000 },
      new Date('2026-07-16T00:00:00.000Z'),
      new Date('2026-07-16T03:00:00.000Z')
    )

    expect(outcome.surface).toBe('monitoring')
    expect(outcome.start.toISOString()).toBe('2026-07-16T00:00:00.000Z')
    expect(outcome.end.toISOString()).toBe('2026-07-16T03:00:00.000Z')
  })
})
