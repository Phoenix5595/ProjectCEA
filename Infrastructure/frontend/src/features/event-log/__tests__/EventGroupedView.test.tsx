import { render, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  EventGroupedView,
  buildGroups,
  withPreallocatedSlots,
} from '../components/EventGroupedView'

import { EVENT_BUCKET_ORDERS, type EventBucketCount } from '../presentation/categoryTheme'

import { makeEventEntry, makeEventEntryWith } from './testFactories'

const NOW = new Date('2026-09-02T12:00:00Z')

describe('EventGroupedView category cards', () => {
  it('renders card bands without wide-only source or reason lines', () => {
    const groups = withPreallocatedSlots(
      buildGroups(
        [
          makeEventEntryWith({
            redisId: '1-0',
            eventId: 'evt-1-0',
            type: 'relay.command_issued',
            category: 'relay',
            entity: { entityType: 'device', entityId: 'fan-1' },
          }),
          makeEventEntryWith({
            redisId: '2-0',
            eventId: 'evt-2-0',
            type: 'relay.command_failed',
            category: 'relay',
            reasonText: 'Failsafe raised',
            entity: { entityType: 'device', entityId: 'fan-0' },
          }),
        ],
        6
      ),
      6
    )
    const cards = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} />
    )
    expect(cards.container.textContent).not.toContain('devices in the last 10 minutes')
    expect(cards.container.textContent).not.toContain('Failsafe raised')
  })

  it('uses only the latest event reason for a category hover', () => {
    const latestReason = 'PID output 65% crossed the >60% relay ON threshold'
    const groups = withPreallocatedSlots(
      buildGroups(
        [
          makeEventEntryWith({
            redisId: '2-0',
            occurredAt: new Date('2026-09-02T12:00:00Z'),
            reasonText: latestReason,
          }),
          makeEventEntryWith({
            redisId: '1-0',
            occurredAt: new Date('2026-09-02T11:59:00Z'),
            reasonText: 'Older event reason',
          }),
        ],
        6
      ),
      6
    )

    const cards = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} />
    )

    const relayCategory = cards.getByTestId('event-group-relay')
    const title = relayCategory.getAttribute('title') ?? ''
    expect(title).toContain('Relay state changed')
    expect(title).toContain(`Latest event in this category — recorded reason: ${latestReason}`)
    expect(relayCategory.getAttribute('title')).not.toContain('Older event reason')
  })

  it('renders exactly six canonical category slots', () => {
    const groups = withPreallocatedSlots(
      buildGroups(
        [
          makeEventEntry('1-0', 'relay.command_issued', 'relay'),
          makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
        ],
        6
      ),
      6
    )
    const cards = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} />
    )
    expect(cards.container.textContent).toContain('No recent events')
    expect(
      cards.container.querySelectorAll(
        '[data-testid^="event-group-"]:not([data-testid="event-group-detail"])'
      )
    ).toHaveLength(6)
    expect(cards.queryByTestId('event-group-rare_event')).not.toBeInTheDocument()
  })

  it('shows one detail line from the latest event with the same hover title', () => {
    const groups = withPreallocatedSlots(
      buildGroups(
        [
          makeEventEntryWith({
            payload: { room: 'Flower Room', state: true },
            entity: { entityType: 'relay_channel', entityId: '2' },
          }),
        ],
        6
      ),
      6
    )

    const cards = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} />
    )

    const detail = within(cards.getByTestId('event-group-relay')).getByTestId('event-group-detail')
    expect(detail.textContent).toBe('Channel 2 · Flower Room · ON')
    expect(detail.getAttribute('title')).toBe(detail.textContent)
  })
})

describe('EventGroupedView adaptive bucket policies', () => {
  const WINDOW_NOW = new Date('2026-09-02T12:00:05Z')

  const CONFLICT_ENTRIES = [
    // Newest first: control setpoint above mutation above ramp above system.
    makeEventEntryWith({
      redisId: '4-0',
      occurredAt: new Date('2026-09-02T12:00:00Z'),
      type: 'control.setpoint_changed',
      category: 'control',
      entity: { entityType: 'device', entityId: 'heater-1' },
    }),
    makeEventEntryWith({
      redisId: '3-0',
      occurredAt: new Date('2026-09-02T11:59:30Z'),
      type: 'config.updated',
      category: 'mutation',
    }),
    makeEventEntryWith({
      redisId: '2-0',
      occurredAt: new Date('2026-09-02T11:59:00Z'),
      type: 'ramp.started',
      category: 'ramp',
      entity: { entityType: 'device', entityId: 'heater-2' },
    }),
    makeEventEntryWith({
      redisId: '1-0',
      occurredAt: new Date('2026-09-02T11:58:30Z'),
      type: 'system.failsafe_raised',
      category: 'system',
    }),
  ]

  function conflictGroups(bucketCount: EventBucketCount) {
    return withPreallocatedSlots(buildGroups(CONFLICT_ENTRIES, bucketCount), bucketCount)
  }

  function renderCards(bucketCount: EventBucketCount) {
    return render(
      <EventGroupedView
        groups={conflictGroups(bucketCount)}
        now={WINDOW_NOW}
        onExpand={() => {}}
        bucketCount={bucketCount}
      />
    )
  }

  it('uses the selected slot order with disabled empty slots and no unknown card', () => {
    for (const bucketCount of [6, 8] as const) {
      const cards = render(
        <EventGroupedView
          groups={withPreallocatedSlots(
            buildGroups(
              [
                makeEventEntry('1-0', 'relay.command_issued', 'relay'),
                makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
              ],
              bucketCount
            ),
            bucketCount
          )}
          now={NOW}
          onExpand={() => {}}
          bucketCount={bucketCount}
        />
      )
      const rows = Array.from(
        cards.container.querySelectorAll<HTMLElement>(
          '[data-testid^="event-group-"]:not([data-testid="event-group-detail"])'
        )
      )
      expect(rows.map(row => row.getAttribute('data-testid'))).toEqual(
        EVENT_BUCKET_ORDERS[bucketCount].map(category => `event-group-${category}`)
      )
      expect(rows).toHaveLength(bucketCount)
      expect(cards.queryByTestId('event-group-rare_event')).not.toBeInTheDocument()

      for (const category of EVENT_BUCKET_ORDERS[bucketCount]) {
        const row = cards.getByTestId(`event-group-${category}`)
        if (category === 'relay') {
          expect(row).toHaveTextContent('Relay command issued')
        } else if (category === 'system') {
          // Unknown categories stay reachable inside the System card.
          expect(row).toHaveTextContent('Custom rare event')
        } else {
          expect(row).toHaveAttribute('aria-disabled', 'true')
          expect(row).toHaveTextContent('No recent events')
        }
      }
      cards.unmount()
    }
  })

  it('merges in the six-bucket policy with split counts and newest labels', () => {
    const cards = renderCards(6)

    // Ramp+control share the Control bucket with merged counts, latest is the
    // newest control event; mutation lands in System together with failsafe.
    const controlRow = cards.getByTestId('event-group-control')
    expect(within(controlRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    expect(cards.queryByTestId('event-group-ramp')).not.toBeInTheDocument()
    expect(cards.queryByTestId('event-group-mutation')).not.toBeInTheDocument()

    const systemRow = cards.getByTestId('event-group-system')
    expect(within(systemRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(systemRow).getByText('Configuration updated')).toBeInTheDocument()

    // Relay stays its own empty slot in the six layout.
    expect(cards.getByTestId('event-group-relay')).toHaveTextContent('No recent events')
    expect(cards.getByTestId('event-group-relay')).toHaveAttribute('aria-disabled', 'true')
    cards.unmount()
  })

  it('splits in the eight-bucket policy with one bucket per raw category', () => {
    const cards = renderCards(8)

    const rows = Array.from(
      cards.container.querySelectorAll(
        '[data-testid^="event-group-"]:not([data-testid="event-group-detail"])'
      )
    ).map(row => (row as HTMLElement).getAttribute('data-testid'))
    expect(rows).toEqual(EVENT_BUCKET_ORDERS[8].map(category => `event-group-${category}`))

    const controlRow = cards.getByTestId('event-group-control')
    expect(within(controlRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    const rampRow = cards.getByTestId('event-group-ramp')
    expect(within(rampRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(rampRow).getByText('Ramp started')).toBeInTheDocument()
    const mutationRow = cards.getByTestId('event-group-mutation')
    expect(within(mutationRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(mutationRow).getByText('Configuration updated')).toBeInTheDocument()
    const systemRow = cards.getByTestId('event-group-system')
    expect(within(systemRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(systemRow).getByText('Failsafe raised')).toBeInTheDocument()

    // Relay, Sensors, Manual override and Alarm stay empty disabled slots.
    for (const category of ['relay', 'sensor', 'manual_override', 'alarm']) {
      expect(cards.getByTestId(`event-group-${category}`)).toHaveAttribute('aria-disabled', 'true')
      expect(cards.getByTestId(`event-group-${category}`)).toHaveTextContent('No recent events')
    }
    cards.unmount()
  })

  it('keeps the grid row count matched to the policy', () => {
    const cardsEight = render(
      <EventGroupedView
        groups={withPreallocatedSlots(
          buildGroups([makeEventEntry('1-0', 'relay.command_issued', 'relay')], 8),
          8
        )}
        now={NOW}
        onExpand={() => {}}
        bucketCount={8}
      />
    )
    const gridEight = cardsEight.getByRole('group', { name: 'Grouped alert console' })
    expect(gridEight.className).toContain('grid-rows-[repeat(8,minmax(min-content,1fr))]')
    expect(gridEight.className).not.toContain('grid-rows-[repeat(6,minmax(min-content,1fr))]')
    cardsEight.unmount()

    const cardsSix = render(
      <EventGroupedView
        groups={withPreallocatedSlots(
          buildGroups([makeEventEntry('1-0', 'relay.command_issued', 'relay')], 6),
          6
        )}
        now={NOW}
        onExpand={() => {}}
        bucketCount={6}
      />
    )
    const gridSix = cardsSix.getByRole('group', { name: 'Grouped alert console' })
    expect(gridSix.className).toContain('grid-rows-[repeat(6,minmax(min-content,1fr))]')
    expect(gridSix.className).toContain('grow')
    cardsSix.unmount()
  })
})
