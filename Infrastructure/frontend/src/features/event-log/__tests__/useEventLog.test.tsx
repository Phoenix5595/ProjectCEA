import { render, screen } from '@testing-library/react'
import { describe, expect, it, beforeEach } from 'vitest'
import { globalEventLogStore, type EventLogEntry } from '../state/eventLogStore'
import { useEventLog, _getSharedStoreForTesting, _resetSharedStoreForTesting } from '../state/useEventLog'

const makeEntry = (redisId: string, type: string, room?: string, cluster?: string): EventLogEntry => ({
  redisId,
  eventId: `evt-${redisId}`,
  type,
  category: 'system',
  occurredAt: new Date('2026-09-02T12:00:00Z'),
  payload: {
    ...(room !== undefined && { room }),
    ...(cluster !== undefined && { cluster }),
  },
})

function TestComponent({ location, cluster }: { location?: string; cluster?: string }) {
  const { entries, connected, error } = useEventLog({ location, cluster })
  return (
    <div>
      <div data-testid="count">{entries.length}</div>
      <div data-testid="connected">{connected ? 'yes' : 'no'}</div>
      <div data-testid="error">{error ?? 'none'}</div>
      {entries.map((e) => (
        <div key={e.eventId}>
          {e.type}
          {typeof e.payload.room === 'string' && <span data-testid={`room-${e.eventId}`}>{e.payload.room}</span>}
          {typeof e.payload.cluster === 'string' && <span data-testid={`cluster-${e.eventId}`}>{e.payload.cluster}</span>}
        </div>
      ))}
    </div>
  )
}

describe('useEventLog', () => {
  beforeEach(() => {
    _resetSharedStoreForTesting()
  })

  it('returns empty entries initially', () => {
    render(<TestComponent />)
    expect(screen.getByTestId('count').textContent).toBe('0')
  })

  it('returns all entries when no location filter', () => {
    const store = _getSharedStoreForTesting()
    store.merge([
      makeEntry('1-0', 'relay.state_changed', 'Flower Room'),
      makeEntry('2-0', 'config.updated', 'Veg Room'),
    ])
    render(<TestComponent />)
    expect(screen.getByTestId('count').textContent).toBe('2')
  })

  it('filters entries by location', () => {
    const store = _getSharedStoreForTesting()
    store.merge([
      makeEntry('1-0', 'relay.state_changed', 'Flower Room'),
      makeEntry('2-0', 'config.updated', 'Veg Room'),
      makeEntry('3-0', 'system.failsafe_raised', 'Flower Room'),
    ])
    render(<TestComponent location="Flower Room" />)
    expect(screen.getByTestId('count').textContent).toBe('2')
    expect(screen.getByText('relay.state_changed')).toBeInTheDocument()
    expect(screen.getByText('system.failsafe_raised')).toBeInTheDocument()
  })

  it('filters entries by location and cluster', () => {
    const store = _getSharedStoreForTesting()
    store.merge([
      makeEntry('1-0', 'relay.state_changed', 'Flower Room', 'front'),
      makeEntry('2-0', 'config.updated', 'Flower Room', 'back'),
      makeEntry('3-0', 'system.failsafe_raised', 'Flower Room', 'front'),
    ])
    render(<TestComponent location="Flower Room" cluster="front" />)
    expect(screen.getByTestId('count').textContent).toBe('2')
  })

  it('returns null error by default', () => {
    render(<TestComponent />)
    expect(screen.getByTestId('error').textContent).toBe('none')
  })

  it('uses global store singleton', () => {
    const store1 = _getSharedStoreForTesting()
    const store2 = globalEventLogStore
    expect(store1).toBe(store2)
  })
})
