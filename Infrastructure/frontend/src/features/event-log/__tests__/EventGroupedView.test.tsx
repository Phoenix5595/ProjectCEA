import { render, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  EventGroupedView,
  buildGroups,
  withPreallocatedSlots,
} from '../components/EventGroupedView'

import {
  EVENT_BUCKET_ORDERS,
  type EventBucketCount,
} from '../presentation/categoryTheme'

import { makeEventEntry, makeEventEntryWith } from './testFactories'

const NOW = new Date('2026-09-02T12:00:00Z')

describe('EventGroupedView compact sidebar mode', () => {
  it('renders compact rows without source/reason/entity detail lines', () => {
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
    const compact = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} compact />
    )
    expect(compact.container.textContent).not.toContain('devices in the last 10 minutes')
    expect(compact.container.textContent).not.toContain('Failsafe raised')

    const wide = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} />
    )
    expect(wide.container.textContent).toContain('devices in the last 10 minutes')
  })

  it('uses only the latest event reason for a compact category hover', () => {
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

    const compact = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} compact />
    )

    const relayCategory = compact.getByTestId('event-group-relay')
    const title = relayCategory.getAttribute('title') ?? ''
    expect(title).toContain('Relay state changed')
    expect(title).toContain(`Latest event in this category — recorded reason: ${latestReason}`)
    expect(relayCategory.getAttribute('title')).not.toContain('Older event reason')
  })

  it('renders exactly six canonical category slots in compact mode', () => {
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
    const compact = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} bucketCount={6} compact />
    )
    expect(compact.container.textContent).toContain('No recent events')
    expect(compact.container.querySelectorAll('[data-testid^="event-group-"]')).toHaveLength(6)
    expect(compact.queryByTestId('event-group-rare_event')).not.toBeInTheDocument()
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

  function renderCompact(bucketCount: EventBucketCount) {
    return render(
      <EventGroupedView
        groups={conflictGroups(bucketCount)}
        now={WINDOW_NOW}
        onExpand={() => {}}
        compact
        bucketCount={bucketCount}
      />
    )
  }

  function renderWide(bucketCount: EventBucketCount) {
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
      const compact = render(
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
          compact
          bucketCount={bucketCount}
        />
      )
      const rows = Array.from(
        compact.container.querySelectorAll<HTMLElement>('[data-testid^="event-group-"]')
      )
      expect(rows.map(row => row.getAttribute('data-testid'))).toEqual(
        EVENT_BUCKET_ORDERS[bucketCount].map(category => `event-group-${category}`)
      )
      expect(rows).toHaveLength(bucketCount)
      expect(compact.queryByTestId('event-group-rare_event')).not.toBeInTheDocument()

      for (const category of EVENT_BUCKET_ORDERS[bucketCount]) {
        const row = compact.getByTestId(`event-group-${category}`)
        if (category === 'relay') {
          expect(row).toHaveTextContent('Relay command issued')
        } else {
          expect(row).toHaveAttribute('aria-disabled', 'true')
          expect(row).toHaveTextContent('No recent events')
        }
      }
      compact.unmount()
    }
  })

  it('merges in the six-bucket policy with split counts and newest labels', () => {
    const compact = renderCompact(6)

    // Ramp+control share the Control bucket with merged counts, latest is the
    // newest control event; mutation lands in System together with failsafe.
    const controlRow = compact.getByTestId('event-group-control')
    expect(within(controlRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    expect(compact.queryByTestId('event-group-ramp')).not.toBeInTheDocument()
    expect(compact.queryByTestId('event-group-mutation')).not.toBeInTheDocument()

    const systemRow = compact.getByTestId('event-group-system')
    expect(within(systemRow).getByText('2', { exact: true })).toBeInTheDocument()
    expect(within(systemRow).getByText('Configuration updated')).toBeInTheDocument()

    // Relay stays its own empty slot in the six layout.
    expect(compact.getByTestId('event-group-relay')).toHaveTextContent('No recent events')
    expect(compact.getByTestId('event-group-relay')).toHaveAttribute('aria-disabled', 'true')
    compact.unmount()

    // Noncompact card: the merged entity window spans both original categories.
    const wide = renderWide(6)
    const wideControl = wide.getByTestId('event-group-control')
    expect(within(wideControl).getByText(/2 devices in the last 10 minutes/)).toBeInTheDocument()
    expect(within(wideControl).getByText(/heater-2, heater-1/)).toBeInTheDocument()
  })

  it('splits in the eight-bucket policy with one bucket per raw category', () => {
    const compact = renderCompact(8)

    const rows = Array.from(
      compact.container.querySelectorAll('[data-testid^="event-group-"]')
    ).map(row => (row as HTMLElement).getAttribute('data-testid'))
    expect(rows).toEqual(EVENT_BUCKET_ORDERS[8].map(category => `event-group-${category}`))

    const controlRow = compact.getByTestId('event-group-control')
    expect(within(controlRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(controlRow).getByText('Control setpoint changed')).toBeInTheDocument()
    const rampRow = compact.getByTestId('event-group-ramp')
    expect(within(rampRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(rampRow).getByText('Ramp started')).toBeInTheDocument()
    const mutationRow = compact.getByTestId('event-group-mutation')
    expect(within(mutationRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(mutationRow).getByText('Configuration updated')).toBeInTheDocument()
    const systemRow = compact.getByTestId('event-group-system')
    expect(within(systemRow).getByText('1', { exact: true })).toBeInTheDocument()
    expect(within(systemRow).getByText('Failsafe raised')).toBeInTheDocument()

    // Relay, Sensors, Manual override and Alarm stay empty disabled slots.
    for (const category of ['relay', 'sensor', 'manual_override', 'alarm']) {
      expect(compact.getByTestId(`event-group-${category}`)).toHaveAttribute(
        'aria-disabled',
        'true'
      )
      expect(compact.getByTestId(`event-group-${category}`)).toHaveTextContent('No recent events')
    }
    compact.unmount()

    // Noncompact control card covers only its own entity now; no multi-entity
    // summary line survives the split.
    const wide = renderWide(8)
    const wideControl = wide.getByTestId('event-group-control')
    expect(
      within(wideControl).queryByText(/devices in the last 10 minutes/)
    ).not.toBeInTheDocument()
    expect(within(wideControl).getByText(/heater-1/)).toBeInTheDocument()
  })

  it('keeps the compact grid class matched to the policy and extras only in wide mode', () => {
    const compactEight = render(
      <EventGroupedView
        groups={withPreallocatedSlots(
          buildGroups(
            [
              makeEventEntry('1-0', 'relay.command_issued', 'relay'),
              makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
            ],
            8
          ),
          8
        )}
        now={NOW}
        onExpand={() => {}}
        compact
        bucketCount={8}
      />
    )
    const gridEight = compactEight.getByRole('group', { name: 'Grouped alert console' })
    expect(gridEight.className).toContain('grid-rows-[repeat(8,minmax(min-content,1fr))]')
    expect(gridEight.className).not.toContain('grid-rows-[repeat(6,minmax(min-content,1fr))]')
    compactEight.unmount()

    const compactSix = render(
      <EventGroupedView
        groups={withPreallocatedSlots(
          buildGroups(
            [
              makeEventEntry('1-0', 'relay.command_issued', 'relay'),
              makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
            ],
            6
          ),
          6
        )}
        now={NOW}
        onExpand={() => {}}
        compact
        bucketCount={6}
      />
    )
    expect(
      compactSix.getByRole('group', { name: 'Grouped alert console' }).className
    ).toContain('grid-rows-[repeat(6,minmax(min-content,1fr))]')
    compactSix.unmount()

    // Wide mode keeps extra raw categories reachable; no row-count class applies.
    const wide = render(
      <EventGroupedView
        groups={withPreallocatedSlots(
          buildGroups(
            [
              makeEventEntry('1-0', 'relay.command_issued', 'relay'),
              makeEventEntry('2-0', 'custom.rare_event', 'rare_event'),
            ],
            8
          ),
          8
        )}
        now={NOW}
        onExpand={() => {}}
        bucketCount={8}
      />
    )
    expect(wide.getByTestId('event-group-rare_event')).toBeInTheDocument()
    expect(wide.getByRole('group', { name: 'Grouped alert console' }).className).not.toContain(
      'grid-rows-'
    )
  })
})

