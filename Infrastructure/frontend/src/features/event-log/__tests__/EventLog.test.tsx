import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { EventLog } from '../components/EventLog'
import { globalEventLogStore } from '../state/eventLogStore'

import { makeEventEntry, makeEventEntryWith } from './testFactories'

describe('EventLog', () => {
  afterEach(() => {
    globalEventLogStore.reset()
  })

  async function showFlat(user: UserEvent) {
    await user.click(screen.getByRole('button', { name: 'All events' }))
  }
  async function selectCompactView(user: UserEvent, label: 'Alerts' | 'All events') {
    const name = new RegExp(`^${label}$`)
    if (!screen.queryByRole('button', { name })) {
      await user.click(screen.getByRole('button', { name: 'Filters' }))
    }
    await user.click(screen.getByRole('button', { name }))
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

  it.each(['Info', 'Warning', 'Error', 'Critical'] as const)(
    'filters identical event types by envelope severity: %s',
    async label => {
      const user = userEvent.setup()
      const entries = (['info', 'warning', 'error', 'critical'] as const).map(
        (entrySeverity, index) =>
          makeEventEntry(`${index + 1}-0`, 'relay.command_failed', 'relay', {}, entrySeverity)
      )
      render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
      await showFlat(user)

      await user.click(screen.getByRole('button', { name: label }))

      expect(screen.getAllByRole('listitem')).toHaveLength(1)
      expect(screen.getByText('Relay command failed')).toBeInTheDocument()
    }
  )

  it.each([
    ['Info', 1],
    ['Error', 0],
  ] as const)(
    'uses envelope severity for relay.command_failed: %s selects %s row(s)',
    async (label, count) => {
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
    }
  )

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

  it('filters entries by category checkbox from the dropdown', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.click(screen.getByRole('button', { name: /Categories & types/ }))
    await user.click(screen.getByRole('checkbox', { name: 'mutation' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(screen.getByText('Configuration updated')).toBeInTheDocument()
  })

  it('filters entries by event type checkbox from the dropdown', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay'),
    ]
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} />)
    await showFlat(user)
    await user.click(screen.getByRole('button', { name: /Categories & types/ }))
    await user.click(screen.getByRole('checkbox', { name: 'relay.command_issued' }))
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

  it('does not show a load-older prompt when older history exists', () => {
    globalEventLogStore.setPaging({ oldestCursor: '1-0', hasMore: true, loadingOlder: false })
    render(<EventLog entries={[]} now={new Date('2026-09-02T12:00:00Z')} />)

    expect(screen.queryByRole('button', { name: /load older/i })).not.toBeInTheDocument()
  })

  it('opens on the grouped console by default with one row per category', () => {
    // Given: entries across three categories with a repeat in the oldest slot.
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay', { device_id: 'fan-2' }),
    ]
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )

    // Then: grouped rows come newest-category-first with counts and latest labels.
    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-mutation')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-relay')).getByText('Relay command issued')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-relay')).getByText('2 events')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-mutation')).getByText('1 event')
    ).toBeInTheDocument()
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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:05Z')} initialView="grouped" />
    )

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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )

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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )
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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )
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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )
    expect(screen.getByRole('group', { name: 'Grouped alert console' }).className).toContain(
      '@2xl:grid-cols-2'
    )
  })

  it('pre-allocates the fixed 2x4 grid with muted slots for empty buckets', () => {
    // Given: a single relay event.
    const entries = [makeEventEntry('1-0', 'relay.state_changed', 'relay')]
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )

    // Then: two columns always, all 8 canonical buckets present, and the
    // seven empty buckets reserve their slot as non-interactive placeholders.
    const grid = screen.getByRole('group', { name: 'Grouped alert console' })
    expect(grid.className).toContain('@2xl:grid-cols-2')
    for (const testid of [
      'relay',
      'sensor',
      'ramp',
      'control',
      'manual_override',
      'mutation',
      'alarm',
      'system',
    ]) {
      expect(grid.querySelector(`[data-testid="event-group-${testid}"]`)).not.toBeNull()
    }
    const emptySensor = screen.getByTestId('event-group-sensor')
    expect(emptySensor).toHaveAttribute('aria-disabled', 'true')
    expect(emptySensor).toHaveTextContent('No recent events')
    expect(within(screen.getByTestId('event-group-relay')).getByText('1 event')).toBeInTheDocument()
  })

  it('renders the fallback row for an unknown garbage category', () => {
    const entries = [makeEventEntry('1-0', 'custom.thing', 'nonsense_category')]
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )
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
    render(
      <EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} initialView="grouped" />
    )

    // Then: two buckets: orange Sensors (2 events) and slate System (1 event).
    expect(
      within(screen.getByTestId('event-group-sensor')).getByText('2 events')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-sensor')).getByText('Device timeout')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-system')).getByText('1 event')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-system')).getByText('Failsafe raised')
    ).toBeInTheDocument()
  })
  it('pages compact flat events five at a time through the oldest sentinel', async () => {
    const entries = Array.from({ length: 50 }, (_, index) => {
      const sentinel = index === 0
      return makeEventEntry(
        `${index + 1}-0`,
        sentinel ? 'custom.sentinel' : 'relay.state_changed',
        sentinel ? 'mutation' : 'relay',
        sentinel ? { device_id: 'oldest-sentinel' } : {}
      )
    })
    const user = userEvent.setup()
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} compact />)

    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    await selectCompactView(user, 'All events')
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 10 · 50 events'
    )
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Previous events page' })).toBeDisabled()

    for (let page = 1; page < 10; page += 1) {
      await user.click(screen.getByRole('button', { name: 'Next events page' }))
    }

    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 10 of 10 · 50 events'
    )
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByText('Custom sentinel')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next events page' })).toBeDisabled()
  })

  it('pages categories, resets on view/category changes, and keeps the ninth category reachable', async () => {
    const entries = [
      ...Array.from({ length: 50 }, (_, index) =>
        makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay')
      ),
      makeEventEntry('51-0', 'custom.rare_event', 'rare_event'),
    ]
    const user = userEvent.setup()
    render(<EventLog entries={entries} now={new Date('2026-09-02T12:00:00Z')} compact />)

    expect(screen.getByTestId('event-groups-page-status')).toHaveTextContent(
      'Groups 1 of 2 · 9 categories'
    )
    await user.click(screen.getByTestId('event-group-relay'))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 10 · 50 events'
    )
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 10 · 50 events'
    )

    await user.click(screen.getByTestId('event-group-collapse'))
    expect(screen.getByTestId('event-groups-page-status')).toHaveTextContent(
      'Groups 1 of 2 · 9 categories'
    )
    await user.click(screen.getByRole('button', { name: 'Next category page' }))
    expect(screen.getByTestId('event-group-rare_event')).toBeInTheDocument()

    await selectCompactView(user, 'All events')
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 11 · 51 events'
    )
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 11 · 51 events'
    )
    await selectCompactView(user, 'Alerts')
    expect(screen.getByTestId('event-groups-page-status')).toHaveTextContent(
      'Groups 1 of 2 · 9 categories'
    )
    await user.click(screen.getByRole('button', { name: 'Next category page' }))
    await user.click(screen.getByTestId('event-group-rare_event'))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 1 · 1 event'
    )
    expect(screen.getByText('Custom rare event')).toBeInTheDocument()
  })

  it('resets compact paging after filters and clamps when live entries shrink', async () => {
    const entries = Array.from({ length: 12 }, (_, index) =>
      makeEventEntry(
        `${index + 1}-0`,
        index === 0 ? 'custom.sentinel' : 'relay.state_changed',
        index === 0 ? 'mutation' : 'relay',
        {},
        index === 0 ? 'critical' : 'info'
      )
    )
    const user = userEvent.setup()
    const { rerender } = render(
      <EventLog
        entries={entries}
        now={new Date('2026-09-02T12:00:00Z')}
        compact
        initialView="flat"
      />
    )

    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 3 · 12 events'
    )
    await user.click(screen.getByRole('button', { name: 'Filters' }))
    await user.click(screen.getByRole('button', { name: 'Critical' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 1 · 1 event'
    )
    expect(screen.getByText('Custom sentinel')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Info' }))
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 3 of 3 · 11 events'
    )
    const shrunkEntries = Array.from({ length: 6 }, (_, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay', {}, 'info')
    )
    rerender(
      <EventLog
        entries={shrunkEntries}
        now={new Date('2026-09-02T12:00:00Z')}
        compact
        initialView="flat"
      />
    )
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 2 · 6 events'
    )
    expect(screen.getByRole('button', { name: 'Next events page' })).toBeDisabled()
  })

  it('opens compact detail in a focus-restoring dialog without expanding the row', async () => {
    const reason = 'PID output crossed the relay threshold'
    const entry = makeEventEntryWith({
      type: 'relay.commanded',
      category: 'relay',
      payload: {
        room: 'Flower Room',
        cluster: 'main',
        device_id: 'heater-1',
        state: 'on',
      },
      entity: { entityType: 'device', entityId: 'heater-1' },
      reasonText: reason,
    })
    const user = userEvent.setup()
    render(
      <EventLog
        entries={[entry]}
        now={new Date('2026-09-02T12:05:00Z')}
        compact
        initialView="flat"
      />
    )

    const openButton = screen.getByTestId('event-detail-opener')
    const row = screen.getByRole('listitem')
    await user.click(openButton)

    const dialog = screen.getByRole('dialog', { name: /Event details — Relay commanded/ })
    expect(dialog).toHaveTextContent(reason)
    expect(dialog).toHaveTextContent('Device heater-1')
    expect(dialog).toHaveTextContent('5m ago')
    expect(within(dialog).getByRole('region', { name: 'Event details' })).toHaveTextContent(
      'heater-1'
    )
    expect(within(row).queryByRole('region', { name: 'Event details' })).not.toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.activeElement).toBe(openButton))
  })
})
