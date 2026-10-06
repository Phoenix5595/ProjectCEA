import { describe, expect, it } from 'vitest'

import {
  scheduleClockInstant,
  scheduleClockMinutes,
  scheduleLocalDates,
  scheduleNextLocalDate,
  scheduleOccurrences,
} from '../scheduleClock'

const ms = (iso: string): number => Date.parse(iso)

describe('scheduleClockMinutes', () => {
  it('extracts Toronto minutes-of-day, not UTC minutes-of-day', () => {
    expect(scheduleClockMinutes(ms('2026-01-01T00:00:00.000Z'))).toBe(19 * 60)
    expect(scheduleClockMinutes(ms('2026-07-01T10:00:00.000Z'))).toBe(6 * 60)
  })
})

describe('scheduleClockInstant', () => {
  it('resolves a stored schedule clock in summer and winter Toronto', () => {
    expect(scheduleClockInstant('2026-07-01', '06:00')).toBe(ms('2026-07-01T10:00:00.000Z'))
    expect(scheduleClockInstant('2026-01-01', '06:00')).toBe(ms('2026-01-01T11:00:00.000Z'))
  })

  it('resolves a fold clock to its first occurrence', () => {
    expect(scheduleClockInstant('2026-11-01', '01:30')).toBe(ms('2026-11-01T05:30:00.000Z'))
  })

  it('resolves a spring-gap clock forward once instead of a UTC fallback', () => {
    expect(scheduleClockInstant('2026-03-08', '02:30')).toBe(ms('2026-03-08T07:30:00.000Z'))
  })

  it('returns null for malformed dates and clocks', () => {
    expect(scheduleClockInstant('2026-7-1', '06:00')).toBeNull()
    expect(scheduleClockInstant('2026-07-01', '6:00')).toBeNull()
    expect(scheduleClockInstant('2026-07-01', '24:00')).toBeNull()
    expect(scheduleClockInstant('2026-02-30', '06:00')).toBeNull()
  })
})

describe('scheduleNextLocalDate', () => {
  it('advances a local date across month and year boundaries', () => {
    expect(scheduleNextLocalDate('2026-02-28')).toBe('2026-03-01')
    expect(scheduleNextLocalDate('2026-12-31')).toBe('2027-01-01')
    expect(scheduleNextLocalDate('bad')).toBeNull()
  })
})

describe('scheduleLocalDates', () => {
  it('includes the local date preceding the window start for overnight anchoring', () => {
    const dates = scheduleLocalDates(ms('2026-01-01T00:00:00.000Z'), ms('2026-01-02T00:00:00.000Z'))
    expect(dates).toEqual(['2025-12-30', '2025-12-31', '2026-01-01'])
  })

  it('returns no dates for an empty window', () => {
    expect(scheduleLocalDates(ms('2026-01-01T00:00:00.000Z'), ms('2026-01-01T00:00:00.000Z'))).toEqual(
      []
    )
  })
})

describe('scheduleOccurrences', () => {
  it('resolves occurrences on the Toronto clock across summer and winter', () => {
    expect(
      scheduleOccurrences(ms('2026-07-01T00:00:00.000Z'), ms('2026-07-02T00:00:00.000Z'), '06:00')
    ).toEqual([ms('2026-07-01T10:00:00.000Z')])
    expect(
      scheduleOccurrences(ms('2026-01-01T00:00:00.000Z'), ms('2026-01-02T00:00:00.000Z'), '06:00')
    ).toEqual([ms('2026-01-01T11:00:00.000Z')])
  })

  it('is half-open: includes a boundary at start and excludes one at end', () => {
    expect(
      scheduleOccurrences(ms('2026-01-01T11:00:00.000Z'), ms('2026-01-01T17:00:00.000Z'), '06:00')
    ).toEqual([ms('2026-01-01T11:00:00.000Z')])
    expect(
      scheduleOccurrences(ms('2026-01-01T05:00:00.000Z'), ms('2026-01-01T11:00:00.000Z'), '06:00')
    ).toEqual([])
  })

  it('expands several occurrences over a multi-day window in order', () => {
    expect(
      scheduleOccurrences(ms('2026-01-01T00:00:00.000Z'), ms('2026-01-03T00:00:00.000Z'), '12:00')
    ).toEqual([ms('2026-01-01T17:00:00.000Z'), ms('2026-01-02T17:00:00.000Z')])
  })

  it('skips an occurrence that does not exist on the spring-gap date', () => {
    expect(
      scheduleOccurrences(ms('2026-03-08T00:00:00.000Z'), ms('2026-03-09T00:00:00.000Z'), '02:30')
    ).toEqual([ms('2026-03-08T07:30:00.000Z')])
  })

  it('resolves one occurrence on a fall-fold date (first EDT instant)', () => {
    expect(
      scheduleOccurrences(ms('2026-11-01T00:00:00.000Z'), ms('2026-11-02T00:00:00.000Z'), '01:30')
    ).toEqual([ms('2026-11-01T05:30:00.000Z')])
  })
})
