export interface EventDisplay {
  readonly label: string
}

const REGISTRY: Readonly<Record<string, string>> = {
  'relay.commanded': 'Relay commanded',
  'relay.observed': 'Relay observed',
  'relay.command_failed': 'Relay command failed',
  'relay.mismatch_detected': 'Relay mismatch detected',
  'relay.mismatch_recovered': 'Relay mismatch recovered',
  'relay.observation_failed': 'Relay observation failed',
  'relay.observation_recovered': 'Relay observation recovered',
  'manual_override.started': 'Manual override started',
  'manual_override.extended': 'Manual override extended',
  'manual_override.expired': 'Manual override expired',
  'manual_override.cancelled': 'Manual override cancelled',
  'manual_override.released': 'Manual override released',
  'manual_override.replaced': 'Manual override replaced',
  'ramp.started': 'Ramp started',
  'ramp.midpoint_reached': 'Ramp midpoint reached',
  'ramp.completed': 'Ramp completed',
  'ramp.interrupted': 'Ramp interrupted',
  'ramp.cancelled': 'Ramp cancelled',
  'ramp.failed': 'Ramp failed',
  'control.adjusted': 'Control adjusted',
  'control.rule_matched': 'Control rule matched',
  'control.failsafe_entered': 'Control failsafe entered',
  'control.failsafe_cleared': 'Control failsafe cleared',
  'control.mode_changed': 'Control mode changed',
  'control.setpoint_changed': 'Control setpoint changed',
  'control.input_missing': 'Control input missing',
  'control.input_recovered': 'Control input recovered',
  'control.saturated': 'Control saturated',
  'control.recovered': 'Control recovered',
  'control.pid_decision': 'Control PID decision',
  'control.auto_pid_decision': 'Control auto PID decision',
  'control.on_off_decision': 'Control on-off decision',
  'control.manual_mode': 'Control manual mode',
  'mutation.created': 'Mutation created',
  'mutation.updated': 'Mutation updated',
  'mutation.deleted': 'Mutation deleted',
  'mutation.action_completed': 'Mutation action completed',
  'alarm.acknowledged': 'Alarm acknowledged',
  'system.failsafe_triggered': 'Failsafe triggered',
  'system.failsafe_raised': 'Failsafe raised',
  'system.failsafe_cleared': 'Failsafe cleared',
  'alarm.triggered': 'Alarm triggered',
  'sensor.degraded': 'Sensor degraded',
  'device.timeout': 'Device timeout',
  'config.updated': 'Configuration updated',
  'mode.transitioned': 'Mode transitioned',
  'schedule.created': 'Schedule created',
  'schedule.updated': 'Schedule updated',
  'schedule.deleted': 'Schedule deleted',
  'relay.state_changed': 'Relay state changed',
  'relay.command_issued': 'Relay command issued',
  'light.intensity_changed': 'Light intensity changed',
  'pid.parameters_changed': 'PID parameters changed',
  'setpoint.updated': 'Setpoint updated',
  'device.registered': 'Device registered',
  'device.removed': 'Device removed',
  'notes.changed': 'Notes changed',
  'calendar.synced': 'Calendar synced',
  'calendar.transition_skipped': 'Calendar transition skipped',
}

function humanizeEventType(eventType: string): string {
  const label = eventType.replace(/[._]+/g, ' ')
  return label.length === 0 ? label : label.charAt(0).toUpperCase() + label.slice(1)
}

export function getEventDisplay(eventType: string): EventDisplay {
  return { label: REGISTRY[eventType] ?? humanizeEventType(eventType) }
}

export function getKnownEventTypes(): string[] {
  return Object.keys(REGISTRY)
}
