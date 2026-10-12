/**
 * Tests for the monitoring time-range toolbar.
 *
 * Covers the happy path (presets, live/pause/resume, absolute
 * fixed range, and URL state) and the DST failure path (spring-forward gap
 * rejection and fall-back fold requiring an explicit first-EDT / second-EST
 * choice). A stateful harness mimics the parent store so URL writes and reads
 * can be observed through a real memory router.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi, type Mock } from 'vitest'

import type { MonitoringRange } from '../../state'
import { TimeRangeToolbar } from '../TimeRangeToolbar'
import type { HistoricalToolbarControl } from '../TimeRangeToolbar'
import {
  MAX_RANGE_MS,
  parseUrlRange,
  validateRange,
} from '../timeRangeToolbar.time'

function liveRange(duration: number): MonitoringRange {
  return { kind: 'live', duration }
}

function fixedRange(start: Date, end: Date): MonitoringRange {
  return { kind: 'fixed', start, end }
}

interface Spies {
  onLive: Mock<(duration: number) => void>
  onFixedRange: Mock<(start: Date, end: Date) => void>
  onPause: Mock<() => void>
  onResume: Mock<() => void>
  onResetZoom: Mock<() => void>
}

interface ToolbarHarnessOptions {
  maxRangeMs?: number | null
  suppressInitialUrlParse?: boolean
  /** Historical mode: ready/title drive the Current run action's state. */
  historical?: { ready: boolean; title: string }
  now?: () => Date
}

function StatefulToolbar({
  initialRange,
  initialIsLive,
  spies,
  maxRangeMs,
  suppressInitialUrlParse,
  historical,
  now,
}: {
  initialRange: MonitoringRange
  initialIsLive: boolean
  spies: Spies
  maxRangeMs?: number | null
  suppressInitialUrlParse?: boolean
  historical?: HistoricalToolbarControl
  now?: () => Date
}) {
  const [range, setRange] = useState(initialRange)
  const [isLive, setIsLive] = useState(initialIsLive)
  return (
    <TimeRangeToolbar
      range={range}
      isLive={isLive}
      onLive={d => {
        spies.onLive(d)
        setRange(liveRange(d))
        setIsLive(true)
      }}
      onFixedRange={(s, e) => {
        spies.onFixedRange(s, e)
        setRange(fixedRange(s, e))
        setIsLive(false)
      }}
      onPause={spies.onPause}
      onResume={spies.onResume}
      onResetZoom={spies.onResetZoom}
      maxRangeMs={maxRangeMs}
      suppressInitialUrlParse={suppressInitialUrlParse}
      historical={historical}
      now={now}
    />
  )
}

interface StatefulRender {
  onLive: Mock<(duration: number) => void>
  onFixedRange: Mock<(start: Date, end: Date) => void>
  onPause: Mock<() => void>
  onResume: Mock<() => void>
  onResetZoom: Mock<() => void>
  onMonitoringRoute: Mock<(start: Date, end: Date) => void>
  onCurrentRun: Mock<() => void>
  router: { navigate: (to: string) => Promise<void> | void; state: { location: { search: string } } }
}

function renderStateful(
  initialRange: MonitoringRange,
  initialIsLive: boolean,
  initialEntries: string[] = ['/'],
  options: ToolbarHarnessOptions = {}
): StatefulRender {
  const spies: Spies = {
    onLive: vi.fn<(duration: number) => void>(),
    onFixedRange: vi.fn<(start: Date, end: Date) => void>(),
    onPause: vi.fn<() => void>(),
    onResume: vi.fn<() => void>(),
    onResetZoom: vi.fn<() => void>(),
  }
  const onMonitoringRoute = vi.fn<(start: Date, end: Date) => void>()
  const onCurrentRun = vi.fn<() => void>()
  const historical: HistoricalToolbarControl | undefined =
    options.historical === undefined
      ? undefined
      : {
          onMonitoringRoute,
          currentRun: {
            ready: options.historical.ready,
            title: options.historical.title,
            onSelect: onCurrentRun,
          },
        }
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <StatefulToolbar
            initialRange={initialRange}
            initialIsLive={initialIsLive}
            spies={spies}
            maxRangeMs={options.maxRangeMs}
            suppressInitialUrlParse={options.suppressInitialUrlParse}
            historical={historical}
            now={options.now}
          />
        ),
      },
    ],
    { initialEntries }
  )
  render(<RouterProvider router={router} />)
  return { ...spies, onMonitoringRoute, onCurrentRun, router }
}

describe('monitoring time-range toolbar', () => {
  it('preserves live fixed pause resume and URL state', async () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    // Live indicator shows LIVE.
    expect(screen.getByRole('status')).toHaveTextContent('LIVE')

    // Clicking a preset calls onLive and pushes the URL.
    fireEvent.click(screen.getByRole('button', { name: '6h' }))
    expect(h.onLive).toHaveBeenCalledWith(6 * 3600_000)
    await waitFor(() => expect(h.router.state.location.search).toContain('range=live-6h'))

    // Pause stops live updates and flips the indicator.
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(h.onPause).toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('PAUSED')

    // Resume restarts live updates.
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(h.onResume).toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('LIVE')

    // Absolute Toronto wall-time entry converts to UTC and pushes the URL.
    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-15T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-15T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-07-15T14:00:00.000Z'),
      new Date('2026-07-15T16:00:00.000Z')
    )
    await waitFor(() => {
      const params = new URLSearchParams(h.router.state.location.search)
      expect(params.get('start')).toBe('2026-07-15T14:00:00.000Z')
      expect(params.get('end')).toBe('2026-07-15T16:00:00.000Z')
    })
  })

  it('restores range from URL on mount and on back/forward', async () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/?range=live-1h'])
    expect(h.onLive).toHaveBeenCalledWith(3600_000)

    h.router.navigate('/?start=2026-07-15T14:00:00.000Z&end=2026-07-15T16:00:00.000Z')
    await waitFor(() =>
      expect(h.onFixedRange).toHaveBeenCalledWith(
        new Date('2026-07-15T14:00:00.000Z'),
        new Date('2026-07-15T16:00:00.000Z')
      )
    )
  })

  it('restores the default live range when history clears fixed range parameters', async () => {
    const start = new Date('2026-07-15T14:00:00.000Z')
    const end = new Date('2026-07-15T16:00:00.000Z')
    const h = renderStateful(fixedRange(start, end), false, [
      '/?start=2026-07-15T14%3A00%3A00.000Z&end=2026-07-15T16%3A00%3A00.000Z',
    ])

    h.router.navigate('/')

    await waitFor(() => expect(screen.getByRole('button', { name: '3h' })).toHaveAttribute('aria-pressed', 'true'))
    expect(h.router.state.location.search).toBe('')
    expect(screen.getByRole('status')).toHaveTextContent('LIVE')
    h.router.navigate('/?start=2026-07-15T14:00:00.000Z&end=2026-07-15T16:00:00.000Z')
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('FIXED'))
    await waitFor(() => {
      expect(screen.getByLabelText('Range start')).toHaveValue('2026-07-15T10:00')
      expect(screen.getByLabelText('Range end')).toHaveValue('2026-07-15T12:00')
    })
    expect(h.router.state.location.search).toContain('start=2026-07-15T14:00:00.000Z')
  })

  it('preserves non-range URL parameters when writing a selected range', async () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, [
      '/?scenario=performance-delay&fixtureSession=toolbar-test',
    ])

    fireEvent.click(screen.getByRole('button', { name: '6h' }))

    await waitFor(() => {
      const params = new URLSearchParams(h.router.state.location.search)
      expect(params.get('range')).toBe('live-6h')
      expect(params.get('scenario')).toBe('performance-delay')
      expect(params.get('fixtureSession')).toBe('toolbar-test')
    })
  })

  it('rejects spring gap and requires explicit fall fold', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    // Spring-forward gap: 2026-03-08 02:30 does not exist in Toronto.
    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-03-08T02:30' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-03-08T04:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/does not exist/)

    // Fall-back fold: 2026-11-01 01:30 occurs twice → require explicit choice.
    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-11-01T01:30' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-11-01T03:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'EDT UTC-04:00' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'EST UTC-05:00' })).toBeInTheDocument()

    // Choosing the first EDT occurrence resolves the ambiguous start.
    fireEvent.click(screen.getByRole('button', { name: 'EDT UTC-04:00' }))
    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-11-01T05:30:00.000Z'),
      new Date('2026-11-01T08:30:00.000Z')
    )
  })

  it('validates range bounds and never calls onFixedRange for invalid input', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    // Range shorter than 5 minutes is rejected.
    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-15T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-15T10:02' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/at least 5 minutes/)

    // Missing input is rejected.
    fireEvent.change(screen.getByLabelText('Range start'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'Apply fixed range' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/Enter both/)
  })

  it('Now returns to live mode with the selected preset duration', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])
    fireEvent.click(screen.getByRole('button', { name: 'Now' }))
    expect(h.onLive).toHaveBeenCalledWith(3 * 3600_000)
  })

  it('exposes the chart reset zoom action', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    fireEvent.click(screen.getByRole('button', { name: 'Reset Zoom' }))

    expect(h.onResetZoom).toHaveBeenCalledOnce()
  })

  it('applies valid Toronto wall-time inputs through the fixed-range callback', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-07-15T10:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-15T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))

    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-07-15T14:00:00.000Z'),
      new Date('2026-07-15T16:00:00.000Z')
    )
  })

  it('highlights and applies the selected live preset', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    fireEvent.click(screen.getByRole('button', { name: '12h' }))

    expect(h.onLive).toHaveBeenCalledWith(12 * 3600_000)
    expect(screen.getByRole('button', { name: '12h' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('reflects an externally forced fixed range in the absolute inputs', () => {
    const onLive = vi.fn<(duration: number) => void>()
    const onFixedRange = vi.fn<(start: Date, end: Date) => void>()
    const onPause = vi.fn<() => void>()
    const onResume = vi.fn<() => void>()
    const start = new Date('2026-07-15T14:00:00.000Z')
    const end = new Date('2026-07-15T16:00:00.000Z')
    const { rerender } = render(
      <MemoryRouter>
        <TimeRangeToolbar
          range={liveRange(3 * 3600_000)}
          isLive
          onLive={onLive}
          onFixedRange={onFixedRange}
          onPause={onPause}
          onResume={onResume}
        />
      </MemoryRouter>
    )

    rerender(
      <MemoryRouter>
        <TimeRangeToolbar
          range={fixedRange(start, end)}
          isLive={false}
          onLive={onLive}
          onFixedRange={onFixedRange}
          onPause={onPause}
          onResume={onResume}
        />
      </MemoryRouter>
    )

    expect(screen.getByLabelText('Range start')).toHaveValue('2026-07-15T10:00')
    expect(screen.getByLabelText('Range end')).toHaveValue('2026-07-15T12:00')
  })
  it('keeps the seven-day fixed boundary exact and rejects reversed or non-finite bounds', () => {
    const start = new Date('2026-07-15T14:00:00.000Z')
    expect(validateRange(start, new Date(start.getTime() + MAX_RANGE_MS))).toBeNull()
    expect(validateRange(start, new Date(start.getTime() + MAX_RANGE_MS + 1))).toBe(
      'Range must not exceed 7 days'
    )
    expect(validateRange(start, new Date(start.getTime() + 5 * 365 * 24 * 3600_000), null)).toBeNull()
    expect(validateRange(new Date(Number.NaN), start, null)).toBe('Range bounds must be valid dates')
    expect(validateRange(start, new Date(start.getTime() - 1), null)).toBe(
      'Range must be at least 5 minutes'
    )
  })

  it('allows long fixed ranges only when opted out and never lifts the live cap', async () => {
    const start = '2024-01-01T15:00:00.000Z'
    const end = '2026-07-15T16:00:00.000Z'
    const params = new URLSearchParams({ start, end })
    expect(parseUrlRange(params).kind).toBe('none')
    expect(parseUrlRange(params, null)).toEqual({
      kind: 'fixed',
      start: new Date(start),
      end: new Date(end),
    })
    expect(parseUrlRange(new URLSearchParams(`range=live-${MAX_RANGE_MS}`), null)).toEqual({
      kind: 'live',
      duration: MAX_RANGE_MS,
    })
    expect(parseUrlRange(new URLSearchParams(`range=live-${MAX_RANGE_MS + 1}`), null)).toEqual({
      kind: 'none',
    })

    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'], { maxRangeMs: null })
    fireEvent.change(screen.getByLabelText('Range start'), { target: { value: '2024-01-01T10:00' } })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-15T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))
    expect(h.onFixedRange).toHaveBeenCalledOnce()
    expect(h.onFixedRange.mock.calls[0]?.[1].getTime() - h.onFixedRange.mock.calls[0]?.[0].getTime())
      .toBeGreaterThan(365 * 24 * 3600_000)
  })

  it('keeps the default fixed cap for Soil-style toolbars', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])
    fireEvent.change(screen.getByLabelText('Range start'), { target: { value: '2026-07-15T10:00' } })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2026-07-23T10:00' } })
    expect(screen.getByRole('button', { name: 'Apply fixed range' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Range must not exceed 7 days')
    expect(h.onFixedRange).not.toHaveBeenCalled()
  })

  it('does not reapply an equal URL range or swallow the next genuine URL write', async () => {
    const start = new Date('2026-07-15T14:00:00.000Z')
    const end = new Date('2026-07-15T16:00:00.000Z')
    const h = renderStateful(fixedRange(start, end), false, [
      '/?start=2026-07-15T14%3A00%3A00.000Z&end=2026-07-15T16%3A00%3A00.000Z',
    ])
    expect(h.onFixedRange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '6h' }))
    expect(h.onLive).toHaveBeenCalledWith(6 * 3600_000)
    await waitFor(() => expect(h.router.state.location.search).toContain('range=live-6h'))
  })

  it('can treat a newly mounted branch toolbar range as authoritative', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/?range=live-6h'], {
      suppressInitialUrlParse: true,
    })
    expect(h.onLive).not.toHaveBeenCalled()
  })
})

describe('historical time-range toolbar', () => {
  const NOW = new Date('2026-03-31T14:00:00.000Z')

  function historicalOptions(ready: boolean): ToolbarHarnessOptions {
    return {
      historical: {
        ready,
        title: ready
          ? 'Apply the Flower Room current run range: calendar start to now'
          : 'No active current run in the Flower Room calendar',
      },
      now: () => NOW,
    }
  }

  it('replaces the live preset group with the fixed history presets', () => {
    renderStateful(liveRange(3 * 3600_000), true, ['/'], historicalOptions(false))

    for (const label of ['1h', '3h', '6h', '12h', '24h', 'Now']) {
      expect(screen.queryByRole('button', { name: label })).toBeNull()
    }
    for (const label of ['5 days', '7 days', '2 weeks', '1 month', '3 months', 'Current run']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
  })

  it('applies fixed now-ended ranges and routes five days to regular Monitoring', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'], historicalOptions(false))

    fireEvent.click(screen.getByRole('button', { name: '5 days' }))
    expect(h.onMonitoringRoute).toHaveBeenCalledWith(
      new Date(NOW.getTime() - 5 * 24 * 3600_000),
      NOW
    )
    expect(h.onFixedRange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '7 days' }))
    expect(h.onFixedRange).toHaveBeenLastCalledWith(
      new Date(NOW.getTime() - 7 * 24 * 3600_000),
      NOW
    )

    fireEvent.click(screen.getByRole('button', { name: '2 weeks' }))
    expect(h.onFixedRange).toHaveBeenLastCalledWith(
      new Date(NOW.getTime() - 14 * 24 * 3600_000),
      NOW
    )

    // Month presets subtract Toronto calendar months with day-of-month clamp.
    fireEvent.click(screen.getByRole('button', { name: '1 month' }))
    expect(h.onFixedRange).toHaveBeenLastCalledWith(new Date('2026-02-28T15:00:00.000Z'), NOW)

    fireEvent.click(screen.getByRole('button', { name: '3 months' }))
    expect(h.onFixedRange).toHaveBeenLastCalledWith(new Date('2025-12-31T15:00:00.000Z'), NOW)

    expect(h.onLive).not.toHaveBeenCalled()
    expect(h.onMonitoringRoute).toHaveBeenCalledTimes(1)
  })

  it('disables the Current run action with the honest reason until the calendar is ready', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'], historicalOptions(false))

    const button = screen.getByRole('button', { name: 'Current run' })
    expect(button).toBeDisabled()

    fireEvent.click(button)
    expect(h.onCurrentRun).not.toHaveBeenCalled()
  })

})
