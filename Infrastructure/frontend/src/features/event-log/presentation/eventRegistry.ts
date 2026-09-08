import { classifySeverity, type SeverityLevel } from './severity'

export interface EventDisplay {
  readonly label: string
  readonly severity: SeverityLevel
}

interface EventRegistryEntry {
  readonly label: string
}

const REGISTRY: Readonly<Record<string, EventRegistryEntry>> = {
  'relay.state_changed': { label: 'Relay state changed' },
  'relay.command_issued': { label: 'Relay command issued' },
  'system.failsafe_raised': { label: 'Failsafe raised' },
  'system.failsafe_cleared': { label: 'Failsafe cleared' },
  'alarm.triggered': { label: 'Alarm triggered' },
  'alarm.acknowledged': { label: 'Alarm acknowledged' },
  'sensor.degraded': { label: 'Sensor degraded' },
  'device.timeout': { label: 'Device timeout' },
  'config.updated': { label: 'Configuration updated' },
  'mode.transitioned': { label: 'Mode transitioned' },
  'schedule.created': { label: 'Schedule created' },
  'schedule.updated': { label: 'Schedule updated' },
  'schedule.deleted': { label: 'Schedule deleted' },
  'light.intensity_changed': { label: 'Light intensity changed' },
  'pid.parameters_changed': { label: 'PID parameters changed' },
  'setpoint.updated': { label: 'Setpoint updated' },
  'device.registered': { label: 'Device registered' },
  'device.removed': { label: 'Device removed' },
  'notes.changed': { label: 'Notes changed' },
  'calendar.synced': { label: 'Calendar synced' },
}

const FALLBACK: EventDisplay = { label: 'Unknown event type', severity: 'info' }

export function getEventDisplay(eventType: string): EventDisplay {
  const entry = REGISTRY[eventType]
  if (entry) return { label: entry.label, severity: classifySeverity(eventType) }
  return FALLBACK
}

export function getKnownEventTypes(): string[] {
  return Object.keys(REGISTRY)
}
