/**
 * Tests for the monitoring time-range toolbar.
 *
 * Covers the happy path (presets, live/pause/resume, Reset Zoom, absolute
 * fixed range, and URL state) and the DST failure path (spring-forward gap
 * rejection and fall-back fold requiring an explicit first-EDT / second-EST
 * choice). A stateful harness mimics the parent store so URL writes and reads
 * can be observed through a real memory router.
 */
import { useState } from 'react'
import { describe, expect, it, vi, type Mock } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import type { MonitoringRange } from '../../state'
import { TimeRangeToolbar } from '../TimeRangeToolbar'

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

function StatefulToolbar({
  initialRange,
  initialIsLive,
  spies,
}: {
  initialRange: MonitoringRange
  initialIsLive: boolean
  spies: Spies
}) {
  const [range, setRange] = useState(initialRange)
  const [isLive, setIsLive] = useState(initialIsLive)
  return (
    <TimeRangeToolbar
      range={range}
      isLive={isLive}
      onLive={(d) => {
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
    />
  )
}

function renderStateful(
  initialRange: MonitoringRange,
  initialIsLive: boolean,
  initialEntries: string[] = ['/'],
): Spies & { router: ReturnType<typeof createMemoryRouter> } {
  const spies: Spies = {
    onLive: vi.fn<(duration: number) => void>(),
    onFixedRange: vi.fn<(start: Date, end: Date) => void>(),
    onPause: vi.fn<() => void>(),
    onResume: vi.fn<() => void>(),
    onResetZoom: vi.fn<() => void>(),
  }
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: (
          <StatefulToolbar initialRange={initialRange} initialIsLive={initialIsLive} spies={spies} />
        ),
      },
    ],
    { initialEntries },
  )
  render(<RouterProvider router={router} />)
  return { ...spies, router }
}

describe('monitoring time-range toolbar', () => {
  it('preserves live fixed pause resume zoom and URL state', async () => {
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

    // Reset Zoom delegates to the chart handle.
    fireEvent.click(screen.getByRole('button', { name: 'Reset Zoom' }))
    expect(h.onResetZoom).toHaveBeenCalled()

    // Absolute Toronto wall-time entry converts to UTC and pushes the URL.
    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '2026-07-15T10:00' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '2026-07-15T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-07-15T14:00:00.000Z'),
      new Date('2026-07-15T16:00:00.000Z'),
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
        new Date('2026-07-15T16:00:00.000Z'),
      ),
    )
  })

  it('rejects spring gap and requires explicit fall fold', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    // Spring-forward gap: 2026-03-08 02:30 does not exist in Toronto.
    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '2026-03-08T02:30' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '2026-03-08T04:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/does not exist/)

    // Fall-back fold: 2026-11-01 01:30 occurs twice → require explicit choice.
    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '2026-11-01T01:30' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '2026-11-01T03:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'EDT UTC-04:00' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'EST UTC-05:00' })).toBeInTheDocument()

    // Choosing the first EDT occurrence resolves the ambiguous start.
    fireEvent.click(screen.getByRole('button', { name: 'EDT UTC-04:00' }))
    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-11-01T05:30:00.000Z'),
      new Date('2026-11-01T08:30:00.000Z'),
    )
  })

  it('validates range bounds and never calls onFixedRange for invalid input', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    // Range shorter than 5 minutes is rejected.
    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '2026-07-15T10:00' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '2026-07-15T10:02' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/at least 5 minutes/)

    // Missing input is rejected.
    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(h.onFixedRange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(/Enter both/)
  })

  it('Now returns to live mode with the selected preset duration', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])
    fireEvent.click(screen.getByRole('button', { name: 'Now' }))
    expect(h.onLive).toHaveBeenCalledWith(3 * 3600_000)
  })

  it('applies valid Toronto wall-time inputs through the fixed-range callback', () => {
    const h = renderStateful(liveRange(3 * 3600_000), true, ['/'])

    fireEvent.change(screen.getByLabelText('Start'), { target: { value: '2026-07-15T10:00' } })
    fireEvent.change(screen.getByLabelText('End'), { target: { value: '2026-07-15T12:00' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(h.onFixedRange).toHaveBeenCalledWith(
      new Date('2026-07-15T14:00:00.000Z'),
      new Date('2026-07-15T16:00:00.000Z'),
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
    const onResetZoom = vi.fn<() => void>()
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
          onResetZoom={onResetZoom}
        />
      </MemoryRouter>,
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
          onResetZoom={onResetZoom}
        />
      </MemoryRouter>,
    )

    expect(screen.getByLabelText('Start')).toHaveValue('2026-07-15T10:00')
    expect(screen.getByLabelText('End')).toHaveValue('2026-07-15T12:00')
  })
})
