import { render, screen, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CalendarOverviewPage from '../CalendarOverviewPage'
import type { EventLogEntry } from '../../../features/event-log/state/eventLogStore'
import { makeEventEntryWith } from '../../../features/event-log/__tests__/testFactories'

vi.mock('../../../services/api', () => ({
  apiClient: { getModeSchedule: vi.fn(() => Promise.resolve(null)) },
}))

vi.mock('../../../hooks/useCalendarEvents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../hooks/useCalendarEvents')>()
  return {
    ...actual,
    useCalendarEvents: () => ({ events: [], loading: false, refresh: () => {} }),
  }
})

const entries: EventLogEntry[] = []
vi.mock('../../../features/event-log/state/useEventLog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../features/event-log/state/useEventLog')>()
  return {
    ...actual,
    useEventLog: () => ({ entries, loading: false, refresh: () => {} }),
  }
})

describe('CalendarOverviewPage event-log time ticking', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-02T12:05:00Z'))
    entries.length = 0
    entries.push(
  makeEventEntryWith({
    type: 'relay.state_changed',
    category: 'relay',
    occurredAt: new Date('2026-09-02T12:05:00Z'),
  }),
)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('advances relative event times every 10 seconds without other changes', async () => {
    render(<CalendarOverviewPage location="Veg Room" />)

    expect(screen.getByText('just now')).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(10_000)
    })

    expect(screen.queryByText('just now')).not.toBeInTheDocument()
    expect(screen.getByText(/10s ago|11s ago/)).toBeInTheDocument()
  })
})
