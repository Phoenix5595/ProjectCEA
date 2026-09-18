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
  severity: 'info',
  occurredAt: new Date('2026-09-02T12:00:00Z'),
  payload: { device_id: 'heater-1', state: 'on' },
  entity: null,
  reasonText: null,
  ...overrides,
})

describe('EventRow', () => {
  it.each([
    ['info', 'Info'],
    ['warning', 'Warning'],
    ['error', 'Error'],
    ['critical', 'Critical'],
  ] as const)('renders the %s badge from the envelope severity', (severity, label) => {
    // Given: the same event type carries one authoritative envelope severity.
    const entry = makeEntry({ severity })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: the label and badge match the envelope, not the event type.
    expect(screen.getByText('Relay state changed')).toBeInTheDocument()
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('uses danger tokens for an Error badge', () => {
    // Given: a command failure is explicitly marked Error by its envelope.
    const entry = makeEntry({ type: 'relay.command_failed', severity: 'error' })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: Error remains distinct and uses the existing danger treatment.
    const badge = screen.getByText('Error')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveClass('bg-status-danger-bg', 'text-status-danger-text', 'border-status-danger-border')
    expect(badge).toHaveAttribute('aria-label', 'Severity: Error')
  })

  it.each([
    ['custom.unknown_event', 'critical', 'Critical'],
    ['system.failsafe_raised', 'info', 'Info'],
    ['alarm.acknowledged', 'warning', 'Warning'],
  ] as const)('does not infer %s severity from its type name', (type, severity, label) => {
    // Given: an event type whose name suggests a different severity.
    const entry = makeEntry({ type, severity })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: the envelope severity is the only source for the visible badge.
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(type)).toBeInTheDocument()
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

  it('shows at a glance what the event is tied to and from where', () => {
    // Given: a relay command row carrying entity, zone, and state context.
    const entry = makeEntry({
      type: 'relay.commanded',
      payload: { room: 'Veg Room', cluster: 'main', state: true },
      entity: { entityType: 'device', entityId: 'Veg Room/main/light_v_3' },
    })

    // When: the row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: the kind, tied device, zone, and state are visible without expanding.
    expect(screen.getByText('Device light_v_3 · Veg Room/main · ON')).toBeInTheDocument()
  })

  it('names the relay channel when the device is unmapped', () => {
    const entry = makeEntry({
      type: 'relay.observed',
      entity: { entityType: 'relay_channel', entityId: '11' },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)
    expect(screen.getByText('Relay channel 11')).toBeInTheDocument()
  })

  it('labels the missing input kind for control.input_missing rows', () => {
    // Given: a heating device whose control input is missing.
    const entry = makeEntry({
      type: 'control.input_missing',
      category: 'control',
      payload: { controller: 'pid', device_type: 'heating', room: 'Flower Room', cluster: 'main' },
      entity: { entityType: 'device', entityId: 'light_f_1' },
      reasonText: 'PID sensor value is unavailable',
    })

    // When: the row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)

    // Then: the controller, device type, missing-input kind, and reason are visible.
    expect(
      screen.getByText('Device light_f_1 · Flower Room/main · controller: pid · device type: heating · no temperature input'),
    ).toBeInTheDocument()
    expect(screen.getByText('PID sensor value is unavailable')).toBeInTheDocument()
  })

  it('maps less common entity types with a human fallback', () => {
    const entry = makeEntry({
      type: 'sensor.degraded',
      category: 'system',
      entity: { entityType: 'soil_probe', entityId: 'soil_temp_front_1' },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:00:05Z')} />)
    expect(screen.getByText('soil probe soil_temp_front_1')).toBeInTheDocument()
  })
})
