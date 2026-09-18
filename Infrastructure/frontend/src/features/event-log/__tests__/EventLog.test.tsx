import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EventLog } from '../components/EventLog'
import { globalEventLogStore, type EventLogEntry } from '../state/eventLogStore'

const makeEntry = (
  redisId: string,
  type: string,
  category: string,
  payload: Record<string, unknown> = {},
  severity: EventLogEntry['severity'] = 'info',
): EventLogEntry => ({
  redisId,
  eventId: `evt-${redisId}`,
  type,
  category,
  severity,
  occurredAt: new Date('2026-09-02T12:00:00Z'),
  payload,
  entity: null,
  reasonText: null,
})

describe('EventLog', () => {
  afterEach(() => {
    globalEventLogStore.reset()
    vi.unstubAllGlobals()
  })
  it('renders an empty state when no entries exist', () => {
    render(<EventLog entries={[]} now={new Date('2026-09-02T12:00:00Z')} />)
    expect(screen.getByText('No events yet')).toBeInTheDocument()
  })

  it('renders all entries as list items', () => {
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('renders newest entries first', () => {
    // Given: entries stored in ascending Redis-ID order (oldest first in the store)
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    const rows = screen.getAllByRole('listitem')

    // Then: the topmost row is the newest event
    expect(within(rows[0]).getByText('Configuration updated')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Relay state changed')).toBeInTheDocument()
  })

  it('filters entries by severity', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEntry('1-0', 'system.failsafe_raised', 'system', {}, 'critical'),
      makeEntry('2-0', 'relay.state_changed', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByRole('button', { name: 'Critical' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Failsafe raised')).toBeInTheDocument()
  })

  it.each(['Info', 'Warning', 'Error', 'Critical'] as const)('filters identical event types by envelope severity: %s', async (label) => {
    const user = userEvent.setup()
    const entries = (['info', 'warning', 'error', 'critical'] as const).map((entrySeverity, index) =>
      makeEntry(`${index + 1}-0`, 'relay.command_failed', 'relay', {}, entrySeverity),
    )
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    await user.click(screen.getByRole('button', { name: label }))

    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Relay command failed')).toBeInTheDocument()
  })

  it.each([
    ['Info', 1],
    ['Error', 0],
  ] as const)('uses envelope severity for relay.command_failed: %s selects %s row(s)', async (label, count) => {
    const user = userEvent.setup()
    const entries = [makeEntry('1-0', 'relay.command_failed', 'relay', {}, 'info')]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    await user.click(screen.getByRole('button', { name: label }))

    if (count === 0) {
      expect(screen.getByText('No events yet')).toBeInTheDocument()
    } else {
      expect(screen.getAllByRole('listitem')).toHaveLength(count)
    }
  })

  it('filters entries by search text', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'heater-1' }),
      makeEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.type(screen.getByRole('searchbox', { name: /filter events/i }), 'relay')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('filters entries by category chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay'),
      makeEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByRole('button', { name: 'mutation', pressed: false }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Configuration updated')).toBeInTheDocument()
  })

  it('filters entries by event type chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay'),
      makeEntry('2-0', 'config.updated', 'mutation'),
      makeEntry('3-0', 'relay.command_issued', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByRole('button', { name: 'relay.command_issued', pressed: false }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Relay command issued')).toBeInTheDocument()
  })

  it('filters entries by room chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEntry('1-0', 'relay.state_changed', 'relay', { room: 'Flower Room' }),
      makeEntry('2-0', 'config.updated', 'mutation', { room: 'Veg Room' }),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByRole('button', { name: 'Veg Room', pressed: false }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Configuration updated')).toBeInTheDocument()
  })

  it('has an accessible heading', () => {
    render(<EventLog entries={[]} now={new Date('2026-09-02T12:00:00Z')} />)
    expect(screen.getByRole('heading', { name: /event log/i })).toBeInTheDocument()
  })

  it('loads the next older page through the visible shared pagination control', async () => {
    // Given: the shared store has an older cursor and the history endpoint is available
    const mockFetch = vi.fn(() => Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({
        items: [], newest_cursor: null, oldest_cursor: null, earliest_cursor: null, has_more: false,
      }),
    }))
    vi.stubGlobal('fetch', mockFetch)
    globalEventLogStore.setPaging({ oldestCursor: '1-0', hasMore: true, loadingOlder: false })
    const user = userEvent.setup()
    render(<EventLog entries={[]} now={new Date('2026-09-02T12:00:00Z')} />)

    // When: the user requests the next page
    await user.click(screen.getByRole('button', { name: 'Load older' }))

    // Then: the cursor is sent and exhaustion removes the control
    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('before=1-0'), expect.anything())
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load older' })).not.toBeInTheDocument())
  })
})
