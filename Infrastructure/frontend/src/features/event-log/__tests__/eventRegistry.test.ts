import { describe, expect, it } from 'vitest'
import { getEventDisplay, getKnownEventTypes } from '../presentation/eventRegistry'

const liveEventLabels = [
  ['relay.commanded', 'Relay commanded'],
  ['relay.observed', 'Relay observed'],
  ['relay.command_failed', 'Relay command failed'],
  ['relay.mismatch_detected', 'Relay mismatch detected'],
  ['relay.mismatch_recovered', 'Relay mismatch recovered'],
  ['relay.observation_failed', 'Relay observation failed'],
  ['relay.observation_recovered', 'Relay observation recovered'],
  ['manual_override.started', 'Manual override started'],
  ['manual_override.extended', 'Manual override extended'],
  ['manual_override.expired', 'Manual override expired'],
  ['manual_override.cancelled', 'Manual override cancelled'],
  ['manual_override.released', 'Manual override released'],
  ['manual_override.replaced', 'Manual override replaced'],
  ['ramp.started', 'Ramp started'],
  ['ramp.midpoint_reached', 'Ramp midpoint reached'],
  ['ramp.completed', 'Ramp completed'],
  ['ramp.interrupted', 'Ramp interrupted'],
  ['ramp.cancelled', 'Ramp cancelled'],
  ['ramp.failed', 'Ramp failed'],
  ['control.adjusted', 'Control adjusted'],
  ['control.rule_matched', 'Control rule matched'],
  ['control.failsafe_entered', 'Control failsafe entered'],
  ['control.failsafe_cleared', 'Control failsafe cleared'],
  ['control.mode_changed', 'Control mode changed'],
  ['control.setpoint_changed', 'Control setpoint changed'],
  ['control.input_missing', 'Control input missing'],
  ['control.input_recovered', 'Control input recovered'],
  ['control.saturated', 'Control saturated'],
  ['control.recovered', 'Control recovered'],
  ['control.pid_decision', 'Control PID decision'],
  ['control.auto_pid_decision', 'Control auto PID decision'],
  ['control.on_off_decision', 'Control on-off decision'],
  ['control.manual_mode', 'Control manual mode'],
  ['mutation.created', 'Mutation created'],
  ['mutation.updated', 'Mutation updated'],
  ['mutation.deleted', 'Mutation deleted'],
  ['mutation.action_completed', 'Mutation action completed'],
  ['alarm.acknowledged', 'Alarm acknowledged'],
  ['system.failsafe_triggered', 'Failsafe triggered'],
  ['system.failsafe_cleared', 'Failsafe cleared'],
] as const

const historicalAliases = [
  ['relay.state_changed', 'Relay state changed'],
  ['relay.command_issued', 'Relay command issued'],
  ['system.failsafe_raised', 'Failsafe raised'],
  ['system.failsafe_cleared', 'Failsafe cleared'],
  ['alarm.triggered', 'Alarm triggered'],
  ['alarm.acknowledged', 'Alarm acknowledged'],
  ['sensor.degraded', 'Sensor degraded'],
  ['device.timeout', 'Device timeout'],
  ['config.updated', 'Configuration updated'],
  ['mode.transitioned', 'Mode transitioned'],
  ['schedule.created', 'Schedule created'],
  ['schedule.updated', 'Schedule updated'],
  ['schedule.deleted', 'Schedule deleted'],
  ['light.intensity_changed', 'Light intensity changed'],
  ['pid.parameters_changed', 'PID parameters changed'],
  ['setpoint.updated', 'Setpoint updated'],
  ['device.registered', 'Device registered'],
  ['device.removed', 'Device removed'],
  ['notes.changed', 'Notes changed'],
  ['calendar.synced', 'Calendar synced'],
] as const

describe('getEventDisplay', () => {
  it('returns the curated label for a skipped calendar transition', () => {
    // Given: the operational event type emitted for an unresolvable calendar destination.

    // When: the presentation registry resolves its display metadata.
    const display = getEventDisplay('calendar.transition_skipped')

    // Then: the event has a stable operator-facing warning label.
    expect(display).toEqual({ label: 'Calendar transition skipped' })
  })

  it.each(liveEventLabels)('returns the curated label for %s', (eventType, label) => {
    const display = getEventDisplay(eventType)
    expect(display).toEqual({ label })
  })

  it.each(historicalAliases)('retains the historical label for %s', (eventType, label) => {
    const display = getEventDisplay(eventType)
    expect(display).toEqual({ label })
  })

  it('humanizes an unregistered valid event type', () => {
    const display = getEventDisplay('custom.unknown_type_xyz')
    expect(display).toEqual({ label: 'Custom unknown type xyz' })
  })

  it('collapses repeated separators when humanizing a valid event type', () => {
    const display = getEventDisplay('custom.unknown__type')
    expect(display).toEqual({ label: 'Custom unknown type' })
  })
})

describe('getKnownEventTypes', () => {
  it('returns a non-empty list of known event type strings', () => {
    const types = getKnownEventTypes()
    expect(types.length).toBeGreaterThan(0)
    expect(types).toContain('relay.state_changed')
    for (const [eventType] of [...liveEventLabels, ...historicalAliases]) {
      expect(types).toContain(eventType)
    }
  })

  it('returns strings only', () => {
    for (const t of getKnownEventTypes()) {
      expect(typeof t).toBe('string')
    }
  })
})
