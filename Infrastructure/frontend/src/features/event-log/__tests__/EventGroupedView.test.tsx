import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EventGroupedView, buildGroups, withPreallocatedSlots } from '../components/EventGroupedView'
import { makeEventEntry, makeEventEntryWith } from './testFactories'

const NOW = new Date('2026-09-02T12:00:00Z')

describe('EventGroupedView compact sidebar mode', () => {
  it('renders compact rows without source/reason/entity detail lines', () => {
    const groups = withPreallocatedSlots(
      buildGroups([
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
      ]),
    )
    const compact = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} compact />,
    )
    expect(compact.container.textContent).not.toContain('devices in the last 10 minutes')
    expect(compact.container.textContent).not.toContain('Failsafe raised')

    const wide = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} />,
    )
    expect(wide.container.textContent).toContain('devices in the last 10 minutes')
  })

  it('keeps every pre-allocated category slot visible in compact mode', () => {
    const groups = withPreallocatedSlots(
      buildGroups([makeEventEntry('1-0', 'relay.command_issued', 'relay')]),
    )
    const compact = render(
      <EventGroupedView groups={groups} now={NOW} onExpand={() => {}} compact />,
    )
    expect(compact.container.textContent).toContain('No recent events')
    expect(compact.container.querySelectorAll('[data-testid^="event-group-"]').length).toBeGreaterThanOrEqual(8)
  })
})
