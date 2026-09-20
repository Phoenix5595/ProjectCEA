import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { EventRow } from '../components/EventRow'
import { makeEventEntryWith } from './testFactories'

describe('EventRow', () => {
  it.each([
    ['info', 'Info'],
    ['warning', 'Warning'],
    ['error', 'Error'],
    ['critical', 'Critical'],
  ] as const)('renders the %s badge from the envelope severity', (severity, label) => {
    // Given: the same event type carries one authoritative envelope severity.
    const entry = makeEventEntryWith({ severity })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)

    // Then: the label and badge match the envelope, not the event type.
    expect(screen.getByText('Relay state changed')).toBeInTheDocument()
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('uses danger tokens for an Error badge', () => {
    // Given: a command failure is explicitly marked Error by its envelope.
    const entry = makeEventEntryWith({ type: 'relay.command_failed', severity: 'error' })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)

    // Then: Error keeps the danger text/border but renders as an outline,
    // distinct from critical's filled treatment.
    const badge = screen.getByText('Error')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveClass('bg-surface-secondary', 'text-status-danger-text', 'border-status-danger-border')
    expect(badge).not.toHaveClass('bg-status-danger-bg')
    expect(badge).toHaveAttribute('aria-label', 'Severity: Error')
  })

  it.each([
    ['custom.unknown_event', 'critical', 'Critical'],
    ['system.failsafe_raised', 'info', 'Info'],
    ['alarm.acknowledged', 'warning', 'Warning'],
  ] as const)('does not infer %s severity from its type name', (type, severity, label) => {
    // Given: an event type whose name suggests a different severity.
    const entry = makeEventEntryWith({ type, severity })

    // When: the event row is rendered.
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)

    // Then: the envelope severity is the only source for the visible badge.
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(type)).toBeInTheDocument()
  })

  it('shows relative time', () => {
    render(<EventRow entry={makeEventEntryWith({})} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.getByText('5m ago')).toBeInTheDocument()
  })

  it('toggles details expansion on click', async () => {
    const user = userEvent.setup()
    render(<EventRow entry={makeEventEntryWith({})} now={new Date('2026-09-02T12:05:00Z')} />)
    const toggle = screen.getByRole('button', { name: /toggle details/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('heater-1')).toBeInTheDocument()
  })

  it('toggles details expansion on Enter key', async () => {
    const user = userEvent.setup()
    render(<EventRow entry={makeEventEntryWith({})} now={new Date('2026-09-02T12:05:00Z')} />)
    const toggle = screen.getByRole('button', { name: /toggle details/i })
    await user.keyboard('{Tab}')
    expect(document.activeElement).toBe(toggle)
    await user.keyboard('{Enter}')
    expect(screen.getByText('heater-1')).toBeInTheDocument()
  })

  it('has an accessible row role', () => {
    render(<EventRow entry={makeEventEntryWith({})} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.getByRole('listitem')).toBeInTheDocument()
  })

  it('shows at a glance what the event is tied to and from where', () => {
    // Given: a relay command row carrying entity, zone, and state context.
    const entry = makeEventEntryWith({
      type: 'relay.commanded',
      payload: { room: 'Veg Room', cluster: 'main', state: true },
      entity: { entityType: 'device', entityId: 'Veg Room/main/light_v_3' },
    })

    // When: the row is rendered.
    const { container } = render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)

    // Then: the kind, tied device, zone, and state are visible without expanding.
    expect(container.textContent).toContain('Device light_v_3 · Veg Room/main · ON')
  })

  it('names the relay channel when the device is unmapped', () => {
    const entry = makeEventEntryWith({
      type: 'relay.observed',
      entity: { entityType: 'relay_channel', entityId: '11' },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.getByText('Relay channel 11')).toBeInTheDocument()
  })

  it('labels the missing input kind for control.input_missing rows', () => {
    // Given: a heating device whose control input is missing.
    const entry = makeEventEntryWith({
      type: 'control.input_missing',
      category: 'control',
      payload: { controller: 'pid', device_type: 'heating', room: 'Flower Room', cluster: 'main' },
      entity: { entityType: 'device', entityId: 'light_f_1' },
      reasonText: 'PID sensor value is unavailable',
    })

    // When: the row is rendered.
    const { container } = render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)

    // Then: the controller, device type, missing-input kind, and reason are visible.
    expect(container.textContent).toContain(
      'Device light_f_1 · Flower Room/main · controller: pid · device type: heating · no temperature input',
    )
    expect(screen.getByText('PID sensor value is unavailable')).toBeInTheDocument()
  })

  it('maps less common entity types with a human fallback', () => {
    const entry = makeEventEntryWith({
      type: 'sensor.degraded',
      category: 'system',
      entity: { entityType: 'soil_probe', entityId: 'soil_temp_front_1' },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.getByText('soil probe soil_temp_front_1')).toBeInTheDocument()
  })

  it('renders a visible absolute time beside the relative chip', () => {
    // Given: a row rendered with an injected deterministic formatter.
    render(
      <EventRow
        entry={makeEventEntryWith({})}
        now={new Date('2026-09-02T12:05:00Z')}
        formatAbsolute={(date) => `ABS:${date.getTime()}`}
      />,
    )

    // Then: the absolute value is visible DOM text, not a hover-only title.
    expect(screen.getByText(/ABS:\d+/)).toBeInTheDocument()
    expect(screen.getByText('5m ago')).toBeInTheDocument()
  })

  it('shows from-to values for a light setpoint change in percent', () => {
    const entry = makeEventEntryWith({
      type: 'control.setpoint_changed',
      category: 'control',
      payload: {
        controller: 'rule',
        device_type: 'light',
        previous_setpoint: 0.442,
        effective_setpoint: 0.438,
      },
      entity: { entityType: 'device', entityId: 'light_v_3' },
    })
    const { container } = render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(container.textContent).toContain('44.2% → 43.8%')
  })

  it('shows from-to values for a heating setpoint change in degrees', () => {
    const entry = makeEventEntryWith({
      type: 'control.setpoint_changed',
      category: 'control',
      payload: {
        controller: 'pid',
        device_type: 'heating',
        previous_setpoint: 22.0,
        effective_setpoint: 22.6,
      },
      entity: { entityType: 'device', entityId: 'heater-1' },
    })
    const { container } = render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(container.textContent).toContain('22 °C → 22.6 °C')
  })

  it('renders no from-to text for legacy payloads without previous_setpoint', () => {
    const entry = makeEventEntryWith({
      type: 'control.setpoint_changed',
      category: 'control',
      payload: { controller: 'pid', device_type: 'light', effective_setpoint: 0.44 },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    expect(screen.queryByText(/→/)).not.toBeInTheDocument()
  })

  it('renders relay-active state text in the green category shade', () => {
    const entry = makeEventEntryWith({
      type: 'relay.state_changed',
      payload: { device_id: 'exhaust-fan', state: true },
      entity: { entityType: 'device', entityId: 'exhaust-fan' },
    })
    render(<EventRow entry={entry} now={new Date('2026-09-02T12:05:00Z')} />)
    const stateBadge = screen.getByText('ON')
    expect(stateBadge).toHaveClass('text-event-relay', 'font-bold')
  })

  it('distinguishes error from critical severity visually', () => {
    // Given: the same source event rendered under error, then critical.
    const first = render(<EventRow entry={makeEventEntryWith({ severity: 'error' })} now={new Date('2026-09-02T12:05:00Z')} />)
    const errorBadge = screen.getByText('Error')
    const errorClasses = errorBadge.className
    first.unmount()
    render(<EventRow entry={makeEventEntryWith({ severity: 'critical' })} now={new Date('2026-09-02T12:05:00Z')} />)
    const criticalBadge = screen.getByText('Critical')

    // Then: the class tuples are distinct and critical is filled/bolder.
    expect(criticalBadge.className).not.toBe(errorClasses)
    expect(criticalBadge).toHaveClass('bg-status-danger-vivid', 'font-bold')
    expect(errorClasses).toContain('bg-surface-secondary')
  })
})
