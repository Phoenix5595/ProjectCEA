import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { EventLog } from '../components/EventLog'
import { globalEventLogStore } from '../state/eventLogStore'

import { makeEventEntry, makeEventEntryWith } from './testFactories'

const NOW = new Date('2026-09-02T12:00:00Z')

describe('EventLog', () => {
  afterEach(() => {
    globalEventLogStore.reset()
  })

  it('renders an empty state when no entries exist', () => {
    render(<EventLog entries={[]} now={NOW} />)
    expect(screen.getByText('No events yet')).toBeInTheDocument()
  })

  it('renders all entries as list items', () => {
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={NOW} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('renders newest entries first', () => {
    // Given: entries stored in ascending Redis-ID order (oldest first in the store)
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
    ]
    render(<EventLog entries={entries} now={NOW} />)
    const rows = screen.getAllByRole('listitem')

    // Then: the topmost row is the newest event
    expect(within(rows[0]).getByText('Configuration updated')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Relay state changed')).toBeInTheDocument()
  })

  it('has an accessible heading', () => {
    render(<EventLog entries={[]} now={NOW} />)
    expect(screen.getByRole('heading', { name: /event log/i })).toBeInTheDocument()
  })

  it('does not show a load-older prompt when older history exists', () => {
    globalEventLogStore.setPaging({ oldestCursor: '1-0', hasMore: true, loadingOlder: false })
    render(<EventLog entries={[]} now={NOW} />)

    expect(screen.queryByRole('button', { name: /load older/i })).not.toBeInTheDocument()
  })

  it('exposes no filter toolbar, view switch or event total', () => {
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { room: 'Flower Room' }),
      makeEventEntry('2-0', 'config.updated', 'mutation', { room: 'Veg Room' }),
    ]
    const { container } = render(<EventLog entries={entries} now={NOW} compact />)

    expect(screen.queryByRole('toolbar', { name: /event filters/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filters' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Alerts' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'All events' })).not.toBeInTheDocument()
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Flower Room', pressed: false })
    ).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\d+ events?/)
    expect(screen.getByTestId('event-log-body')).toHaveClass('overflow-y-auto')
  })

  it('opens on the category cards with one row per category', () => {
    // Given: entries across two categories with a repeat in the oldest slot.
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay', { device_id: 'fan-2' }),
    ]
    render(<EventLog entries={entries} now={NOW} compact />)

    // Then: one card per category with counts and latest labels.
    // Raw mutation merges into the System presentation bucket.
    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-system')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-relay')).getByText('Relay command issued')
    ).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-relay')).getByText('2')).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-system')).getByText('1')).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('drills into a category list and returns to the cards', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'fan-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'relay.command_issued', 'relay', { device_id: 'fan-2' }),
    ]
    render(<EventLog entries={entries} now={NOW} compact />)

    // When: the relay category card is opened.
    await user.click(screen.getByTestId('event-group-relay'))

    // Then: the section body is the full relay list, newest first.
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Relay command issued')).toBeInTheDocument()
    expect(screen.queryByTestId('event-group-relay')).not.toBeInTheDocument()

    // When: the collapse control is used.
    await user.click(screen.getByTestId('event-group-collapse'))

    // Then: the category cards are restored.
    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('pre-allocates the six canonical buckets with muted slots for empty ones', () => {
    // Given: a single relay event.
    const entries = [makeEventEntry('1-0', 'relay.state_changed', 'relay')]
    render(<EventLog entries={entries} now={NOW} compact />)

    // Then: six canonical buckets on the card grid, and the five
    // empty buckets reserve their slot as non-interactive placeholders.
    const grid = screen.getByRole('group', { name: 'Grouped alert console' })
    for (const testid of ['relay', 'sensor', 'control', 'manual_override', 'alarm', 'system']) {
      expect(grid.querySelector(`[data-testid="event-group-${testid}"]`)).not.toBeNull()
    }
    const emptySensor = screen.getByTestId('event-group-sensor')
    expect(emptySensor).toHaveAttribute('aria-disabled', 'true')
    expect(emptySensor).toHaveTextContent('No recent events')
    expect(within(screen.getByTestId('event-group-relay')).getByText('1')).toBeInTheDocument()
  })

  it('keeps unknown category events reachable inside the System card', async () => {
    const user = userEvent.setup()
    const entries = [makeEventEntry('1-0', 'custom.thing', 'nonsense_category')]
    render(<EventLog entries={entries} now={NOW} compact />)

    expect(screen.queryByTestId('event-group-nonsense_category')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('event-group-system'))
    expect(screen.getByText('Custom thing')).toBeInTheDocument()
  })

  it('splits system-category sensor events into their own Sensors row', () => {
    // Given: platform and sensor-health events that share the system category.
    const entries = [
      makeEventEntry('1-0', 'sensor.degraded', 'system', { device_id: 'dry-bulb-front' }),
      makeEventEntry('2-0', 'system.failsafe_raised', 'system', {}, 'critical'),
      makeEventEntry('3-0', 'device.timeout', 'system', { device_id: 'soil-sensor-1' }),
    ]
    render(<EventLog entries={entries} now={NOW} compact />)

    // Then: two buckets: orange Sensors (2 events) and slate System (1 event).
    expect(within(screen.getByTestId('event-group-sensor')).getByText('2')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-sensor')).getByText('Device timeout')
    ).toBeInTheDocument()
    expect(within(screen.getByTestId('event-group-system')).getByText('1')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-system')).getByText('Failsafe raised')
    ).toBeInTheDocument()
  })

  it('pages a category list five at a time through the oldest sentinel', async () => {
    const entries = Array.from({ length: 50 }, (_, index) => {
      const sentinel = index === 0
      return makeEventEntry(
        `${index + 1}-0`,
        sentinel ? 'custom.sentinel' : 'relay.state_changed',
        'relay',
        sentinel ? { device_id: 'oldest-sentinel' } : {}
      )
    })
    const user = userEvent.setup()
    render(<EventLog entries={entries} now={NOW} compact />)

    expect(screen.getByTestId('event-group-relay')).toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-relay'))
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

  it('keeps the category overview fixed at six cards', async () => {
    const entries = [
      makeEventEntry('1-0', 'relay.state_changed', 'relay'),
      makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
    ]
    const user = userEvent.setup()
    const { container } = render(<EventLog entries={entries} now={NOW} compact />)

    expect(
      container.querySelectorAll(
        '[data-testid^="event-group-"]:not([data-testid="event-group-detail"])'
      )
    ).toHaveLength(6)
    expect(screen.queryByTestId('event-groups-page-status')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /category page/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('event-events-page-status')).not.toBeInTheDocument()

    // The unknown category is reachable inside the System drill-down.
    await user.click(screen.getByTestId('event-group-system'))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 1 · 1 event'
    )
    expect(screen.getByText('Custom rare event')).toBeInTheDocument()
  })

  it('merges control/ramp and system/mutation while keeping both originals reachable', async () => {
    const user = userEvent.setup()
    const entries = [
      makeEventEntry('1-0', 'control.setpoint_changed', 'control', { device_id: 'heater-1' }),
      makeEventEntry('2-0', 'config.updated', 'mutation'),
      makeEventEntry('3-0', 'ramp.started', 'ramp', { device_id: 'heater-2' }),
      makeEventEntry('4-0', 'system.failsafe_raised', 'system', {}, 'critical'),
    ]
    render(<EventLog entries={entries} now={NOW} compact />)

    // Then: one card each for the merged buckets, with combined counts.
    const controlRow = screen.getByTestId('event-group-control')
    expect(within(controlRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Ramp started')).toBeInTheDocument()
    const systemRow = screen.getByTestId('event-group-system')
    expect(within(systemRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(systemRow).getByText('Failsafe raised')).toBeInTheDocument()

    // When: the Control bucket expands, both original categories are listed newest-first.
    await user.click(controlRow)
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('Ramp started')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Control setpoint changed')).toBeInTheDocument()
  })

  it('clamps compact paging when live entries shrink', async () => {
    const entries = Array.from({ length: 12 }, (_, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay', {
        device_id: `fan-${index}`,
      })
    )
    const user = userEvent.setup()
    const { rerender } = render(<EventLog entries={entries} now={NOW} compact />)

    await user.click(screen.getByTestId('event-group-relay'))
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 3 · 12 events'
    )

    const shrunkEntries = Array.from({ length: 6 }, (_, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay', {}, 'info')
    )
    rerender(<EventLog entries={shrunkEntries} now={NOW} compact />)
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 2 · 6 events'
    )
    expect(screen.getByRole('button', { name: 'Next events page' })).toBeDisabled()
  })

  it('opens compact detail in the inline region with focus restore and no row expansion', async () => {
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
    render(<EventLog entries={[entry]} now={new Date('2026-09-02T12:05:00Z')} compact />)

    await user.click(screen.getByTestId('event-group-relay'))
    const openButton = screen.getByTestId('event-detail-opener')
    const row = screen.getByRole('listitem')
    await user.click(openButton)

    const detail = screen.getByRole('region', { name: 'Selected event details' })
    expect(detail).toHaveTextContent('Event details — Relay commanded')
    expect(detail).toHaveTextContent(reason)
    expect(detail).toHaveTextContent('Device heater-1')
    expect(detail).toHaveTextContent('5m ago')
    expect(detail).toHaveTextContent('Category: Relay (relay)')
    expect(within(detail).getByRole('region', { name: 'Event details' })).toHaveTextContent(
      'heater-1'
    )
    expect(within(row).queryByRole('region', { name: 'Event details' })).not.toBeInTheDocument()
    expect(document.activeElement).toHaveTextContent('Event details — Relay commanded')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.activeElement).toBe(openButton))
  })
})

describe('EventLog bucket policies', () => {
  afterEach(() => {
    globalEventLogStore.reset()
  })

  const NOW = () => new Date('2026-09-02T12:00:00Z')

  const CONFLICT_ENTRIES = [
    makeEventEntry('1-0', 'system.failsafe_raised', 'system', {}, 'critical'),
    makeEventEntry('2-0', 'ramp.started', 'ramp', { device_id: 'heater-2' }),
    makeEventEntry('3-0', 'config.updated', 'mutation'),
    makeEventEntry('4-0', 'control.setpoint_changed', 'control', { device_id: 'heater-1' }),
  ]

  it('keeps every raw category reachable when the eight-bucket policy is active', async () => {
    const user = userEvent.setup()
    render(<EventLog entries={CONFLICT_ENTRIES} now={NOW()} compact bucketCount={8} />)

    // Then: each raw category owns exactly one populated card.
    const controlRow = screen.getByTestId('event-group-control')
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    expect(within(controlRow).getByText('1', { exact: true })).toBeInTheDocument()
    const rampRow = screen.getByTestId('event-group-ramp')
    expect(within(rampRow).getByText('Ramp started')).toBeInTheDocument()
    const mutationRow = screen.getByTestId('event-group-mutation')
    expect(within(mutationRow).getByText('Configuration updated')).toBeInTheDocument()
    const systemRow = screen.getByTestId('event-group-system')
    expect(within(systemRow).getByText('Failsafe raised')).toBeInTheDocument()

    // Expansion and collapse remain per raw category.
    await user.click(controlRow)
    let rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText('Control setpoint changed')).toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-collapse'))

    await user.click(screen.getByTestId('event-group-ramp'))
    rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText('Ramp started')).toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-collapse'))

    await user.click(screen.getByTestId('event-group-mutation'))
    rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText('Configuration updated')).toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-collapse'))

    await user.click(screen.getByTestId('event-group-system'))
    rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    expect(within(rows[0]).getByText('Failsafe raised')).toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-collapse'))
  })

  it('returns to the merged six-bucket overview from an expanded ramp with an open detail', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <EventLog entries={CONFLICT_ENTRIES} now={NOW()} compact bucketCount={8} />
    )

    // When: ramp is expanded with its inline detail open.
    await user.click(screen.getByTestId('event-group-ramp'))
    await user.click(screen.getByTestId('event-detail-opener'))
    expect(screen.getByRole('region', { name: 'Selected event details' })).toBeInTheDocument()

    rerender(<EventLog entries={CONFLICT_ENTRIES} now={NOW()} compact bucketCount={6} />)

    // Then: the card overview returns under the merged policy and the detail is gone.
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Selected event details' })).not.toBeInTheDocument()
    const controlRow = screen.getByTestId('event-group-control')
    expect(within(controlRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    expect(screen.queryByTestId('event-group-ramp')).not.toBeInTheDocument()
  })

  it('returns to the split eight-bucket overview from an expanded merged Control', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <EventLog entries={CONFLICT_ENTRIES} now={NOW()} compact bucketCount={6} />
    )

    // When: the merged Control bucket is expanded, then the policy widens.
    await user.click(screen.getByTestId('event-group-control'))
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    rerender(<EventLog entries={CONFLICT_ENTRIES} now={NOW()} compact bucketCount={8} />)

    // Then: the card overview returns and every raw category is split out
    // again — not a silently narrowed flat list.
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(screen.getByTestId('event-group-control')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-ramp')).toBeInTheDocument()
    expect(screen.getByTestId('event-group-mutation')).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-control')).getByText('Control setpoint changed')
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('event-group-ramp')).getByText('Ramp started')
    ).toBeInTheDocument()
  })

  it('resets paging and detail when the bucket policy changes inside a category list', async () => {
    const entries = Array.from({ length: 12 }, (_, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay', {
        device_id: `fan-${index}`,
      })
    )
    const user = userEvent.setup()
    const { rerender } = render(<EventLog entries={entries} now={NOW()} compact bucketCount={6} />)
    await user.click(screen.getByTestId('event-group-relay'))
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 3 · 12 events'
    )
    await user.click(screen.getAllByTestId('event-detail-opener')[0])
    expect(screen.getByRole('region', { name: 'Selected event details' })).toBeInTheDocument()

    rerender(<EventLog entries={entries} now={NOW()} compact bucketCount={8} />)

    // Then: the cards return, the detail is gone, and paging restarts on page one.
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Selected event details' })).not.toBeInTheDocument()
    await user.click(screen.getByTestId('event-group-relay'))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 1 of 3 · 12 events'
    )
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
  })

  it('preserves paging, expansion, and detail across a same-policy re-render', async () => {
    const entries = Array.from({ length: 12 }, (_, index) =>
      makeEventEntry(`${index + 1}-0`, 'relay.state_changed', 'relay', {
        device_id: `fan-${index}`,
      })
    )
    const user = userEvent.setup()
    const view = <EventLog entries={entries} now={NOW()} compact bucketCount={8} />
    const { rerender } = render(view)
    await user.click(screen.getByTestId('event-group-relay'))
    await user.click(screen.getByRole('button', { name: 'Next events page' }))
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 3 · 12 events'
    )
    await user.click(screen.getAllByTestId('event-detail-opener')[0])
    expect(screen.getByRole('region', { name: 'Selected event details' })).toBeInTheDocument()

    rerender(view)

    // Then: an eight→eight re-render is a policy no-op; nothing resets.
    expect(screen.getByTestId('event-events-page-status')).toHaveTextContent(
      'Page 2 of 3 · 12 events'
    )
    expect(screen.getByRole('region', { name: 'Selected event details' })).toBeInTheDocument()
  })
})
