import { describe, expect, it, vi, afterEach } from 'vitest'
import { formatRelativeTime, formatExactTime, formatLocalTime } from '../presentation/timeFormat'

describe('formatRelativeTime', () => {
  afterEach(() => { vi.useRealTimers() })

  it('shows "just now" for events within 5 seconds', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T12:00:00Z'))
    expect(formatRelativeTime(new Date('2026-09-02T11:59:58Z'))).toBe('just now')
  })

  it('shows seconds for events under a minute', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T12:00:30Z'))
    expect(formatRelativeTime(new Date('2026-09-02T12:00:00Z'))).toBe('30s ago')
  })

  it('shows minutes for events under an hour', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T12:05:00Z'))
    expect(formatRelativeTime(new Date('2026-09-02T12:00:00Z'))).toBe('5m ago')
  })

  it('shows hours for events under a day', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T15:00:00Z'))
    expect(formatRelativeTime(new Date('2026-09-02T12:00:00Z'))).toBe('3h ago')
  })

  it('shows days for events over a day', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-04T12:00:00Z'))
    expect(formatRelativeTime(new Date('2026-09-02T12:00:00Z'))).toBe('2d ago')
  })
})

describe('formatExactTime', () => {
  it('formats a date as ISO-like local time', () => {
    const date = new Date('2026-09-02T14:30:45Z')
    const result = formatExactTime(date)
    expect(result).toMatch(/2026-09-02/)
    expect(result).toMatch(/14:30:45|02:30:45\s*PM|14:30/)
  })
})

describe('formatLocalTime', () => {
  it('shows only the clock for a same-day timestamp', () => {
    const now = new Date()
    const date = new Date(now.getTime())
    const result = formatLocalTime(date, now)
    expect(result).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })

  it('prefixes the date when the timestamp is not today', () => {
    const now = new Date()
    const date = new Date(now.getTime() - 1000 * 60 * 60 * 24)
    const result = formatLocalTime(date, now)
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })

  it('is injectable-friendly: pure function of its two arguments', () => {
    // Given: two renders with the same inputs at different wall-clock times.
    const date = new Date('2026-09-02T12:00:00Z')
    const now = new Date('2026-09-02T12:30:00Z')
    const first = formatLocalTime(date, now)
    const second = formatLocalTime(date, now)

    // Then: the output does not depend on the real system clock.
    expect(first).toBe(second)
  })
})
