import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { EventRow } from '../components/EventRow'
import type { EventLogEntry } from '../state/eventLogStore'

const makeEntry = (overrides: Partial<EventLogEntry> = {}): EventLogEntry => ({
  redisId: '1-0',
  eventId: 'evt-1',
  type: 'relay.state_changed',
  category: 'relay',
  occurredAt: new Date('2026-09-02T12:00:00Z'),
  payload: { device_id: 'heater-1', state: 'on' },
  ...overrides,
})

describe('EventRow', () => {
  it('renders the event label and severity text', () => {
    render(<EventRow entry={makeEntry()} now={new Date('2026-09-02T12:00:05Z')} />)
    expect(screen.getByText('Relay state changed')).toBeInTheDocument()
    expect(screen.getByText('Info')).toBeInTheDocument()
  })

  it('renders a severity badge with non-color visual treatment', () => {
    render(<EventRow entry={makeEntry({ type: 'system.failsafe_raised' })} now={new Date('2026-09-02T12:00:05Z')} />)
    const badge = screen.getByText('Critical')
    expect(badge).toBeInTheDocument()
    expect(badge.closest('span')).toHaveAttribute('aria-label')
  })

  it('shows relative time', () => {
    render(<EventRow entry={makeEntry()} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.getByText('5m ago')).toBeInTheDocument()
  })

  it('toggles details expansion on click', async () => {
    const user = userEvent.setup()
    render(<EventRow entry={makeEntry()} now={new Date('2026-09-02T12:00:05Z')} />)
    const toggle = screen.getByRole('button', { name: /toggle details/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('heater-1')).toBeInTheDocument()
  })

  it('toggles details expansion on Enter key', async () => {
    const user = userEvent.setup()
    render(<EventRow entry={makeEntry()} now={new Date('2026-09-02T12:00:05Z')} />)
    const toggle = screen.getByRole('button', { name: /toggle details/i })
    await user.keyboard('{Tab}')
    expect(document.activeElement).toBe(toggle)
    await user.keyboard('{Enter}')
    expect(screen.getByText('heater-1')).toBeInTheDocument()
  })

  it('has an accessible row role', () => {
    render(<EventRow entry={makeEntry()} now={new Date('2026-09-02T12:00:05Z')} />)
    expect(screen.getByRole('listitem')).toBeInTheDocument()
  })
})
