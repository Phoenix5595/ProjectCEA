import { describe, expect, it } from 'vitest'

import {
  TORONTO_TZ,
  parseWallInput,
  resolveRecurringWallTime,
  resolveWallTime,
  resolveWallTimeWithChoice,
  torontoDateString,
  torontoWallComponents,
} from '../torontoWallTime'

describe('parseWallInput', () => {
  it('parses a zero-padded wall-time string into components', () => {
    expect(parseWallInput('2026-07-15T10:30')).toEqual({ y: 2026, mo: 6, d: 15, h: 10, min: 30 })
  })

  it('rejects malformed, out-of-range and non-padded input', () => {
    expect(parseWallInput('2026-07-15 10:30')).toBeNull()
    expect(parseWallInput('2026-7-15T10:30')).toBeNull()
    expect(parseWallInput('2026-07-15T24:00')).toBeNull()
    expect(parseWallInput('2026-13-15T10:30')).toBeNull()
    expect(parseWallInput('2026-07-15T10:60')).toBeNull()
    expect(parseWallInput('not-a-time')).toBeNull()
  })
})

describe('resolveWallTime', () => {
  it('resolves a summer Toronto clock through EDT (10:00Z for 06:00)', () => {
    const resolved = resolveWallTime({ y: 2026, mo: 6, d: 1, h: 6, min: 0 })
    expect(resolved).toEqual({ kind: 'valid', utc: new Date('2026-07-01T10:00:00.000Z') })
  })

  it('resolves a winter Toronto clock through EST (11:00Z for 06:00)', () => {
    const resolved = resolveWallTime({ y: 2026, mo: 0, d: 1, h: 6, min: 0 })
    expect(resolved).toEqual({ kind: 'valid', utc: new Date('2026-01-01T11:00:00.000Z') })
  })

  it('rejects a spring-forward gap time without inventing a resolution', () => {
    const resolved = resolveWallTime({ y: 2026, mo: 2, d: 8, h: 2, min: 30 })
    expect(resolved).toEqual({ kind: 'nonexistent' })
  })

  it('reports both fall-fold occurrences with EDT first and EST second', () => {
    const resolved = resolveWallTime({ y: 2026, mo: 10, d: 1, h: 1, min: 30 })
    expect(resolved.kind).toBe('ambiguous')
    if (resolved.kind !== 'ambiguous') return
    expect(resolved.firstUtc.toISOString()).toBe('2026-11-01T05:30:00.000Z')
    expect(resolved.secondUtc.toISOString()).toBe('2026-11-01T06:30:00.000Z')
  })

  it('treats a calendar date that does not exist as nonexistent', () => {
    expect(resolveWallTime({ y: 2026, mo: 1, d: 30, h: 6, min: 0 })).toEqual({
      kind: 'nonexistent',
    })
  })
})

describe('resolveWallTimeWithChoice', () => {
  it('keeps the ambiguous result when no choice is supplied', () => {
    const resolved = resolveWallTimeWithChoice({ y: 2026, mo: 10, d: 1, h: 1, min: 30 }, null)
    expect(resolved.kind).toBe('ambiguous')
  })

  it('applies the explicit fold choice to the matching occurrence', () => {
    const first = resolveWallTimeWithChoice({ y: 2026, mo: 10, d: 1, h: 1, min: 30 }, 'first')
    const second = resolveWallTimeWithChoice({ y: 2026, mo: 10, d: 1, h: 1, min: 30 }, 'second')
    expect(first).toEqual({ kind: 'valid', utc: new Date('2026-11-01T05:30:00.000Z') })
    expect(second).toEqual({ kind: 'valid', utc: new Date('2026-11-01T06:30:00.000Z') })
  })

  it('resolves unambiguous times regardless of the choice', () => {
    const resolved = resolveWallTimeWithChoice({ y: 2026, mo: 6, d: 1, h: 6, min: 0 }, 'second')
    expect(resolved).toEqual({ kind: 'valid', utc: new Date('2026-07-01T10:00:00.000Z') })
  })
})

describe('resolveRecurringWallTime', () => {
  it('resolves a valid recurring clock to the same UTC with no assumption', () => {
    expect(resolveRecurringWallTime({ y: 2026, mo: 6, d: 1, h: 6, min: 0 })).toEqual({
      utc: new Date('2026-07-01T10:00:00.000Z'),
      dstAssumption: null,
    })
  })

  it('resolves a fold to the first EDT occurrence', () => {
    expect(resolveRecurringWallTime({ y: 2026, mo: 10, d: 1, h: 1, min: 30 })).toEqual({
      utc: new Date('2026-11-01T05:30:00.000Z'),
      dstAssumption: 'fold-first',
    })
  })

  it('resolves a spring gap forward to the shifted wall clock', () => {
    const resolved = resolveRecurringWallTime({ y: 2026, mo: 2, d: 8, h: 2, min: 30 })
    expect(resolved.dstAssumption).toBe('gap-forward')
    expect(resolved.utc?.toISOString()).toBe('2026-03-08T07:30:00.000Z')
  })
})

describe('torontoWallComponents and torontoDateString', () => {
  it('extracts the Toronto wall components of a UTC instant in winter and summer', () => {
    expect(torontoWallComponents(Date.parse('2026-01-01T00:00:00.000Z'))).toEqual({
      y: 2025,
      mo: 11,
      d: 31,
      h: 19,
      min: 0,
    })
    expect(torontoWallComponents(Date.parse('2026-07-01T10:00:00.000Z'))).toEqual({
      y: 2026,
      mo: 6,
      d: 1,
      h: 6,
      min: 0,
    })
  })

  it('formats the Toronto local date containing the instant', () => {
    expect(torontoDateString(Date.parse('2026-01-01T00:00:00.000Z'))).toBe('2025-12-31')
    expect(torontoDateString(Date.parse('2026-07-01T03:59:59.000Z'))).toBe('2026-06-30')
    expect(torontoDateString(Date.parse('2026-07-01T04:00:00.000Z'))).toBe('2026-07-01')
  })

  it('exports the Toronto zone identifier used by both features', () => {
    expect(TORONTO_TZ).toBe('America/Toronto')
  })
})
