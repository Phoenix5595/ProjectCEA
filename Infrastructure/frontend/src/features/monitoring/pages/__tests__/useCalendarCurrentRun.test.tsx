/**
 * Tests for the calendar current-run hook.
 *
 * Covers room-scoped availability, Toronto-midnight conversion, request
 * cancellation and rejection of late answers after changing rooms.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MonitoringRequestContext } from '../../api'
import { useCalendarCurrentRun } from '../useCalendarCurrentRun'

type CurrentRunOptions = { signal?: AbortSignal; scenario?: string; fixtureSession?: string }

const { getCalendarCurrentRun } = vi.hoisted(() => ({
  getCalendarCurrentRun: vi.fn<(location: string, options?: CurrentRunOptions) => Promise<unknown>>(),
}))

vi.mock('../../../../services/api', () => ({ apiClient: { getCalendarCurrentRun } }))

/** A promise that never settles, for in-flight assertions. */
const pending = <T,>(): Promise<T> => Promise.withResolvers<T>().promise

function Harness({
  location,
  context,
}: {
  location: string | null
  context?: MonitoringRequestContext
}) {
  const control = useCalendarCurrentRun(location, context)
  return (
    <div>
      <span data-testid="ready">{String(control.ready)}</span>
      <span data-testid="title">{control.title}</span>
      <span data-testid="start">{control.start?.toISOString() ?? ''}</span>
    </div>
  )
}

describe('useCalendarCurrentRun', () => {
  beforeEach(() => {
    getCalendarCurrentRun.mockReset()
  })

  it('never fetches while no historical room owns the control', () => {
    render(<Harness location={null} />)

    expect(getCalendarCurrentRun).not.toHaveBeenCalled()
    expect(screen.getByTestId('ready')).toHaveTextContent('false')
  })


  it('resolves the calendar start to Toronto midnight in UTC', async () => {
    getCalendarCurrentRun.mockResolvedValue({
      location: 'Flower Room',
      grow_plan_id: 'plan-1',
      start_date: '2026-09-01',
      end_date: '2026-12-01',
    })

    render(<Harness location="Flower Room" context={{ scenario: 'no-current', fixtureSession: 'hist-1' }} />)

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('start')).toHaveTextContent('2026-09-01T04:00:00.000Z')
  })

  it('aborts the in-flight request when the view unmounts', () => {
    getCalendarCurrentRun.mockReturnValue(pending())

    const { unmount } = render(<Harness location="Flower Room" />)
    const signal = getCalendarCurrentRun.mock.calls[0]?.[1]?.signal
    expect(signal?.aborted).toBe(false)

    unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('ignores a late run for the previous room after a room change', async () => {
    const flower = Promise.withResolvers<unknown>()
    getCalendarCurrentRun.mockReturnValueOnce(flower.promise).mockResolvedValueOnce({
      location: 'Veg Room',
      grow_plan_id: 'plan-veg',
      start_date: '2026-09-20',
      end_date: '2026-12-20',
    })

    const { rerender } = render(<Harness location="Flower Room" />)
    const flowerSignal = getCalendarCurrentRun.mock.calls[0]?.[1]?.signal

    rerender(<Harness location="Veg Room" />)

    await waitFor(() => expect(screen.getByTestId('start')).toHaveTextContent('2026-09-20T04:00:00.000Z'))
    expect(flowerSignal?.aborted).toBe(true)

    await act(async () => {
      flower.resolve({
        location: 'Flower Room',
        grow_plan_id: 'plan-flower',
        start_date: '2026-08-01',
        end_date: '2026-11-01',
      })
      await flower.promise
    })

    // The late Flower Room answer must not replace the Veg Room run.
    expect(screen.getByTestId('start')).toHaveTextContent('2026-09-20T04:00:00.000Z')
  })

  it.each([{ minutes: 4, ready: false }, { minutes: 5, ready: true }])(
    'respects the graph minimum for a run only $minutes minutes old',
    async ({ minutes, ready }) => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date(Date.UTC(2026, 6, 15, 4, minutes)))
      try {
        getCalendarCurrentRun.mockResolvedValue({
          location: 'Flower Room', grow_plan_id: 'plan-1',
          start_date: '2026-07-15', end_date: '2026-08-15',
        })
        await act(async () => { render(<Harness location="Flower Room" />) })
        expect(screen.getByTestId('ready').textContent).toBe(String(ready))
      } finally {
        vi.useRealTimers()
      }
    }
  )
})
