import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EventLog } from '../components/EventLog'
import { globalEventLogStore } from '../state/eventLogStore'
import { makeEventEntry } from './testFactories'

describe('EventLog', () => {
  afterEach(() => {
    globalEventLogStore.reset()
    vi.unstubAllGlobals()
  })

  async function showFlat(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'All events' }))
  }

  it('renders an empty state when no entries exist', () => {
    render(<EventLog entries={[]} now={new Date('2026-09-02T12:00:00Z')} />)
    expect(screen.getByText('No events yet')).toBeInTheDocument()
  })

  it('renders all entries as list items', async () => {
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    const user = userEvent.setup()
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('renders newest entries first', async () => {
    // Given: entries stored in ascending Redis-ID order (oldest first in the store)
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    const user = userEvent.setup()
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    const rows = screen.getAllByRole('listitem')

    // Then: the topmost row is the newest event
    expect(within(rows[0]).getByText('Configuration updated')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Relay state changed')).toBeInTheDocument()
  })

  it('filters entries by severity', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'system.failsafe_raised', 'system', {}, 'critical'),
      makeEventEntry('2-0', 'relay.state_changed', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.click(screen.getByRole('button', { name: 'Critical' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Failsafe raised')).toBeInTheDocument()
  })

  it.each(['Info', 'Warning', 'Error', 'Critical'] as const)('filters identical event types by envelope severity: %s', async (label) => {
    const user = userEvent.setup()
    const entries = (['info', 'warning', 'error', 'critical'] as const).map((entrySeverity, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.command_failed', 'relay', {}, entrySeverity),
    )
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)

    await user.click(screen.getByRole('button', { name: label }))

    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Relay command failed')).toBeInTheDocument()
  })

  it.each([
    ['Info', 1],
    ['Error', 0],
  ] as const)('uses envelope severity for relay.command_failed: %s selects %s row(s)', async (label, count) => {
    const user = userEvent.setup()
    const entries = [makeEventEntry('1-0', 'relay.command_failed', 'relay', {}, 'info')]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)

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
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'heater-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.type(screen.getByRole('searchbox', { name: /filter events/i }), 'relay')
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('filters entries by category chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.click(screen.getByRole('button', { name: 'mutation', pressed: false }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Configuration updated')).toBeInTheDocument()
  })

  it('filters entries by event type chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.click(screen.getByRole('button', { name: 'relay.command_issued', pressed: false }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Relay command issued')).toBeInTheDocument()
  })

  it('filters entries by room chip', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { room: 'Flower Room' }),
      makeEventEntry('2-0', 'config.updated', 'mutation', { room: 'Veg Room' }),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
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

  it('opens on the grouped console by default with one row per category', () => {
    // Given: entries across three categories with a repeat in the oldest slot.
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay', { device_id: 'fan-2' }),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    // Then: grouped rows come newest-category-first with counts and latest labels.
    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-mutation')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-relay')).getByText('Relay command issued')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-relay')).getByText('2 events')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-mutation')).getByText('1 event')).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('shows the concurrent-entity summary for a multi-device ramp category', () => {
    // Given: three distinct devices with events of one category inside the window.
    const base = new Date('2026-09-02T12:00:00Z')
    const entries = ['light_v_1', 'light_v_2', 'light_v_3'].map((entityId, index) => ({
      ...makeEventEntry(`${index + 1}-0`, 'ramp.started', 'ramp', { ramp_type: 'light' }, 'info'),
      occurredAt: new Date(base.getTime() + index * 1000),
      entity: { entityType: 'device', entityId },
    }))
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: one grouped row summarises all three devices in its context line.
    const row = screen.getByTestId('event-group-ramp')
    expect(within(row).getByText(/3 devices in the last 10 minutes/)).toBeInTheDocument()
    expect(within(row).getByText(/light_v_1, light_v_2, light_v_3/)).toBeInTheDocument()
  })

  it('expands a category into its full newest-first list and collapses back', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay', { device_id: 'fan-2' }),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    // When: the relay category row is expanded.
    await user.click(screen.getByTestId('event-group-relay'))

    // Then: the section body is the full relay list, newest first.
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Relay command issued')).toBeInTheDocument()

    // When: the collapse control is used.
    await user.click(screen.getByTestId('event-group-collapse'))

    // Then: the grouped view is restored.
    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('shows the flat newest-first list through the All events toggle', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByTestId('event-group-mutation'))

    // When: the flat view is selected.
    await user.click(screen.getByRole('button', { name: 'All events' }))

    // Then: the full newest-first list is shown and the category row is gone.
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.queryByTestId('event-group-mutation')).not.toBeInTheDocument()
  })

  it('applies the severity filter in the grouped view', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'system.failsafe_raised', 'system', {}, 'critical'),
      makeEventEntry('2-0', 'relay.state_changed', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await user.click(screen.getByRole('button', { name: 'Critical' }))

    // Then: only the matching category is populated; the relay slot is an
    // empty pre-allocated placeholder.
    expect(screen.getByTestId('event-group-system')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-relay')).toHaveAttribute('aria-disabled', 'true')
  })

  it('applies two columns on wide viewports when categories exceed four', () => {
    const entries = (
      [
        ['relay', 'relay.state_changed'],
        ['manual_override', 'manual_override.started'],
        ['ramp', 'ramp.started'],
        ['control', 'control.setpoint_changed'],
        ['mutation', 'config.updated'],
      ] as const
    ).map(([category, type], index) => makeEventEntry(`${index + 1}-0`, type, category))
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    expect(screen.getByRole('group', { name: 'Grouped alert console' }).className).toContain('lg:grid-cols-2')
  })

  it('pre-allocates the fixed 2x4 grid with muted slots for empty buckets', () => {
    // Given: a single relay event.
    const entries = [makeEventEntry('1-0', 'relay.state_changed', 'relay')]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    // Then: two columns always, all 8 canonical buckets present, and the
    // seven empty buckets reserve their slot as non-interactive placeholders.
    const grid = screen.getByRole('group', { name: 'Grouped alert console' })
    expect(grid.className).toContain('lg:grid-cols-2')
    for (const testid of ['relay', 'sensor', 'ramp', 'control', 'manual_override', 'mutation', 'alarm', 'system']) {
      expect(grid.querySelector(`[data-testid="event-group-${testid}"]`)).not.toBeNull()
    }
    const emptySensor = screen.getByTestId('event-group-sensor')
    expect(emptySensor).toHaveAttribute('aria-disabled', 'true')
    expect(emptySensor).toHaveTextContent('No recent events')
    expect(within(screen.getByTestId('event-group-relay')).getByText('1 event')).toBeInTheDocument()
  })

  it('renders the fallback row for an unknown garbage category', () => {
    const entries = [makeEventEntry('1-0', 'custom.thing', 'nonsense_category')]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    const row = screen.getByTestId('event-group-nonsense_category')
    expect(within(row).getByText('Other')).toBeInTheDocument()
  })

  it('splits system-category sensor events into their own Sensors row', () => {
    // Given: platform and sensor-health events that share the system category.
    const entries = [
      makeEventEntry('1-0', 'sensor.degraded', 'system', { device_id: 'dry-bulb-front' }),
      makeEventEntry('2-0', 'system.failsafe_raised', 'system', {}, 'critical'),
      makeEventEntry('3-0', 'device.timeout', 'system', { device_id: 'soil-sensor-1' }),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)

    // Then: two buckets: orange Sensors (2 events) and slate System (1 event).
    expect(within(screen.getByTestId('event-group-sensor')).getByText('2 events')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-sensor')).getByText('Device timeout')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-system')).getByText('1 event')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-system')).getByText('Failsafe raised')).toBeInTheDocument()
  })
})
