/**
 * Behavior tests for the frontend overview bucket picker/clamp core.
 *
 * Mirrors the backend `history_buckets.py` contracts: budget bounds, the
 * whole-hour display ladder (1..12h) picked by actual intersecting count,
 * honest rejection when even the 12-hour cap cannot fit, nonaligned edge
 * clipping, ordered nonoverlapping grids, and the drill-down
 * center/clamp/shift behavior used by overview drill-downs.
 */
import { describe, expect, it } from 'vitest'

import {
  chooseHistoryBucket,
  drillDownRange,
  historyBucketBounds,
  historyOverviewBucketBounds,
  HistoryOverviewRangeError,
} from '../buckets'
import type { HistoryBucketMetadata } from '../buckets'

function date(value: string): Date {
  return new Date(value)
}

const HOUR_MS = 3_600_000
const DAY_MS = 24 * HOUR_MS

function assertOrderedClippedGrid(
  range: { start: Date; end: Date },
  buckets: readonly { start: Date; end: Date; partial: boolean }[],
  budget: number
): void {
  expect(buckets.length).toBeGreaterThan(0)
  expect(buckets.length).toBeLessThanOrEqual(budget)
  const list = buckets
  let previous: { start: Date; end: Date } | null = null
  for (const entry of list) {
    if (entry.partial) {
      expect(
        entry.start.getTime() === range.start.getTime() ||
          entry.end.getTime() === range.end.getTime()
      ).toBe(true)
    }
    expect(entry.start.getTime()).toBeGreaterThanOrEqual(range.start.getTime())
    expect(entry.end.getTime()).toBeLessThanOrEqual(range.end.getTime())
    expect(entry.end.getTime()).toBeGreaterThan(entry.start.getTime())
    if (previous !== null) {
      expect(entry.start.getTime()).toBe(previous.end.getTime())
    }
    previous = entry
  }
  expect(list[0]?.start.getTime()).toBe(range.start.getTime())
  expect(list[list.length - 1]?.end.getTime()).toBe(range.end.getTime())
}

describe('historyOverviewBucketBounds', () => {
  it('rejects invalid budgets and unordered or invalid ranges', () => {
    const start = date('2026-01-01T00:00:00.000Z')
    const end = date('2026-01-02T00:00:00.000Z')
    expect(() => historyOverviewBucketBounds(start, end, 9)).toThrow(HistoryOverviewRangeError)
    expect(() => historyOverviewBucketBounds(start, end, 10.5)).toThrow(HistoryOverviewRangeError)
    expect(() => historyOverviewBucketBounds(start, end, 4001)).toThrow(
      HistoryOverviewRangeError
    )
    expect(() => historyOverviewBucketBounds(end, start, 600)).toThrow(HistoryOverviewRangeError)
    expect(() => historyOverviewBucketBounds(new Date(NaN), end, 10)).toThrow(
      HistoryOverviewRangeError
    )
    const accepted = chooseHistoryBucket(start, end, 600)
    expect(accepted).toEqual({ kind: 'fixed', seconds: 3600 })
  })

  it('clips nonaligned fixed edges into the requested range', () => {
    const start = date('2026-05-04T03:27:11.123Z')
    const end = date('2026-05-06T19:13:08.321Z')
    const result = historyOverviewBucketBounds(start, end, 600)
    expect(result.bucket).toEqual({ kind: 'fixed', seconds: 3600 })
    assertOrderedClippedGrid({ start, end }, result.buckets, 600)
    for (const entry of result.buckets) {
      if (!entry.partial) {
        expect(entry.start.getTime() % HOUR_MS).toBe(0)
        expect(entry.end.getTime() - entry.start.getTime()).toBe(HOUR_MS)
      }
    }
    expect(result.buckets[0]?.partial).toBe(true)
    expect(result.buckets[result.buckets.length - 1]?.partial).toBe(true)
  })

  it('selects the finest whole-hour width by actual intersecting count', () => {
    const start = date('2026-01-01T00:00:00.000Z')
    const end = date('2026-01-02T01:00:00.000Z')
    const hourly = historyOverviewBucketBounds(start, end, 25)
    expect(hourly.bucket).toEqual({ kind: 'fixed', seconds: 3600 })
    expect(hourly.buckets).toHaveLength(25)
    const twoHourly = historyOverviewBucketBounds(start, end, 24)
    expect(twoHourly.bucket).toEqual({ kind: 'fixed', seconds: 7200 })
    expect(twoHourly.buckets).toHaveLength(13)
  })

  it('clips nonaligned outer buckets at the selected 8-hour width', () => {
    const start = date('2026-01-05T00:30:00.000Z')
    const end = date('2026-07-05T00:30:00.000Z')
    const result = historyOverviewBucketBounds(start, end, 600)
    expect(result.bucket).toEqual({ kind: 'fixed', seconds: 28800 })
    expect(result.buckets).toHaveLength(544)
    assertOrderedClippedGrid({ start, end }, result.buckets, 600)
    expect(result.buckets[0]?.partial).toBe(true)
    expect(result.buckets[0]?.start.getTime()).toBe(start.getTime())
    const last = result.buckets[result.buckets.length - 1]
    expect(last?.partial).toBe(true)
    expect(last?.end.getTime()).toBe(end.getTime())
    for (const entry of result.buckets) {
      if (!entry.partial) {
        expect(entry.start.getTime() % (8 * HOUR_MS)).toBe(0)
        expect(entry.end.getTime() - entry.start.getTime()).toBe(8 * HOUR_MS)
      }
    }
  })

  it('keeps six calendar months inside the 600-point default budget with 8-hour buckets', () => {
    const start = date('2026-03-01T00:00:00.000Z')
    const end = date('2026-09-01T00:00:00.000Z')
    const result = historyOverviewBucketBounds(start, end, 600)
    expect(result.bucket).toEqual({ kind: 'fixed', seconds: 28800 })
    expect(result.buckets).toHaveLength(552)
    for (const entry of result.buckets) expect(entry.partial).toBe(false)
  })

  it('picks the finest whole-hour width at the 1000-point budget', () => {
    const start = date('2026-03-01T00:00:00.000Z')
    const end = date('2026-09-01T00:00:00.000Z')
    const result = historyOverviewBucketBounds(start, end, 1000)
    expect(result.bucket).toEqual({ kind: 'fixed', seconds: 18000 })
    expect(result.buckets.length).toBeLessThanOrEqual(1000)
    expect(historyBucketBounds(start, end, { kind: 'fixed', seconds: 14400 })).toHaveLength(1104)
  })

  it('falls back past 8 hours to the 12-hour cap instead of widening the budget', () => {
    const start = date('2026-03-01T00:00:00.000Z')
    const end = date('2026-09-01T00:00:00.000Z')
    const exact = historyOverviewBucketBounds(start, end, 552)
    expect(exact.bucket).toEqual({ kind: 'fixed', seconds: 28800 })
    expect(exact.buckets).toHaveLength(552)
    const next = historyOverviewBucketBounds(start, end, 551)
    expect(next.bucket).toEqual({ kind: 'fixed', seconds: 32400 })
    expect(next.buckets.length).toBeLessThanOrEqual(551)
    const capped = historyOverviewBucketBounds(start, end, 380)
    expect(capped.bucket).toEqual({ kind: 'fixed', seconds: 43200 })
    expect(capped.buckets).toHaveLength(368)
  })

  it('rejects a range whose 12-hour bucket count exceeds the budget', () => {
    const start = date('2026-03-01T00:00:00.000Z')
    const end = date('2026-09-01T00:00:00.000Z')
    expect(() => historyOverviewBucketBounds(start, end, 367)).toThrow(
      'history range and point budget cannot fit the maximum 12-hour bucket size'
    )
    expect(() => historyOverviewBucketBounds(start, end, 367)).toThrow(HistoryOverviewRangeError)
    const multiYear = {
      start: date('2015-01-01T00:00:00.000Z'),
      end: date('2026-01-01T00:00:00.000Z'),
    }
    for (const budget of [10, 600, 1000]) {
      expect(() => historyOverviewBucketBounds(multiYear.start, multiYear.end, budget)).toThrow(
        'history range and point budget cannot fit the maximum 12-hour bucket size'
      )
    }
  })

  it('rejects widths outside the whole-hour 1..12 ladder at runtime', () => {
    const start = date('2026-01-01T00:00:00.000Z')
    const end = date('2026-01-02T00:00:00.000Z')
    for (const bucket of [
      { kind: 'calendar', months: 1, time_zone: 'UTC' },
      { kind: 'fixed', seconds: 1800 },
      { kind: 'fixed', seconds: 0 },
      { kind: 'fixed', seconds: 86400 },
      { kind: 'fixed', seconds: 2419200 },
      { kind: 'fixed', seconds: 3601 },
    ]) {
      expect(() =>
        historyBucketBounds(start, end, bucket as unknown as HistoryBucketMetadata)
      ).toThrow(HistoryOverviewRangeError)
    }
  })

  it('keeps aligned fixed ranges free of partial flags and exact widths', () => {
    const start = date('2026-01-05T00:00:00.000Z')
    const end = date('2026-01-05T04:00:00.000Z')
    const result = historyOverviewBucketBounds(start, end, 600)
    expect(result.buckets).toHaveLength(4)
    for (const entry of result.buckets) expect(entry.partial).toBe(false)
  })

  it.each([
    { budget: 10, end: '2026-01-06T00:00:00.000Z', seconds: 43200 },
    { budget: 600, end: '2026-01-26T00:00:00.000Z', seconds: 3600 },
    { budget: 1000, end: '2026-02-11T00:00:00.000Z', seconds: 3600 },
  ])(
    'keeps a retained range contiguous inside a $budget-point budget',
    ({ budget, end, seconds }) => {
      const range = { start: date('2026-01-01T00:00:00.000Z'), end: date(end) }
      const result = historyOverviewBucketBounds(range.start, range.end, budget)
      expect(result.bucket).toEqual({ kind: 'fixed', seconds })
      assertOrderedClippedGrid(range, result.buckets, budget)
    }
  )
})

describe('drillDownRange', () => {
  const parent = { start: date('2019-01-01T00:00:00.000Z'), end: date('2025-01-01T00:00:00.000Z') }

  it('turns a click into a one-day centered window', () => {
    const click = date('2024-05-04T12:00:00.000Z').getTime()
    const detail = drillDownRange(parent, { start: new Date(click), end: new Date(click) })
    expect(detail.start.getTime()).toBe(click - 12 * HOUR_MS)
    expect(detail.end.getTime() - detail.start.getTime()).toBe(DAY_MS)
  })

  it('shifts a near-edge click fully inside the parent', () => {
    const nearEnd = new Date(parent.end.getTime() - 1)
    const detail = drillDownRange(parent, { start: nearEnd, end: nearEnd })
    expect(detail.end.getTime()).toBe(parent.end.getTime())
    expect(detail.end.getTime() - detail.start.getTime()).toBe(DAY_MS)
  })

  it('clamps oversized drag zooms to seven days centered on the selection', () => {
    const selectionStart = date('2023-06-01T00:00:00.000Z')
    const detail = drillDownRange(parent, {
      start: selectionStart,
      end: new Date(selectionStart.getTime() + 30 * DAY_MS),
    })
    const center = selectionStart.getTime() + 15 * DAY_MS
    expect(detail.end.getTime() - detail.start.getTime()).toBe(7 * DAY_MS)
    expect((detail.start.getTime() + detail.end.getTime()) / 2).toBe(center)
  })

  it('raises sub-five-minute selections to the five-minute minimum', () => {
    const click = date('2024-02-11T08:16:03.000Z').getTime()
    const detail = drillDownRange(parent, {
      start: new Date(click),
      end: new Date(click + 4 * 60_000),
    })
    expect(detail.end.getTime() - detail.start.getTime()).toBe(5 * 60_000)
    expect(
      Math.abs((detail.start.getTime() + detail.end.getTime()) / 2 - (click + 2 * 60_000))
    ).toBeLessThanOrEqual(1)
  })

  it('returns an already-fitting aligned selection unchanged', () => {
    const parent = {
      start: date('2026-06-01T00:00:00.000Z'),
      end: date('2026-07-01T00:00:00.000Z'),
    }
    const block = historyOverviewBucketBounds(parent.start, parent.end, 600)
    expect(block.bucket).toEqual({ kind: 'fixed', seconds: 7200 })
    const exact = block.buckets[0]
    expect(exact).toBeDefined()
    expect(exact?.partial).toBe(false)
    const detail = drillDownRange(
      parent,
      exact === undefined ? parent : { start: exact.start, end: exact.end }
    )
    expect(detail.start.getTime()).toBe(date('2026-06-01T00:00:00.000Z').getTime())
    expect(detail.end.getTime()).toBe(date('2026-06-01T02:00:00.000Z').getTime())
  })

  it('keeps windows inside the parent when a drag straddles its end', () => {
    const parent = {
      start: date('2019-01-01T00:00:00.000Z'),
      end: date('2025-01-01T00:00:00.000Z'),
    }
    const straddleEnd = new Date(parent.end.getTime() + 3 * DAY_MS)
    const detail = drillDownRange(parent, {
      start: new Date(straddleEnd.getTime() - 2 * DAY_MS),
      end: straddleEnd,
    })
    expect(detail.end.getTime()).toBe(parent.end.getTime())
    expect(detail.end.getTime() - detail.start.getTime()).toBe(2 * DAY_MS)
  })

  it('preserves a wider Historical selection instead of forcing a seven-day detail', () => {
    const start = date('2023-06-01T00:00:00.000Z')
    const end = new Date(start.getTime() + 30 * DAY_MS)
    const selected = drillDownRange(parent, { start, end }, 'historical')
    expect(selected.start.getTime()).toBe(start.getTime())
    expect(selected.end.getTime()).toBe(end.getTime())
  })

  it('shifts a wider Historical selection inside its parent without losing its span', () => {
    const parent = {
      start: date('2026-01-01T00:00:00.000Z'),
      end: date('2026-02-01T00:00:00.000Z'),
    }
    const start = date('2026-01-25T00:00:00.000Z')
    const end = date('2026-02-10T00:00:00.000Z')
    const selected = drillDownRange(parent, { start, end }, 'historical')
    expect(selected.end.getTime()).toBe(parent.end.getTime())
    expect(selected.end.getTime() - selected.start.getTime()).toBe(end.getTime() - start.getTime())
  })
})
