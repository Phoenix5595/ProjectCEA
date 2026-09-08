export type SeverityLevel = 'critical' | 'warning' | 'info'

const CRITICAL_PREFIXES = ['system.failsafe', 'alarm.'] as const
const WARNING_PREFIXES = ['sensor.degraded', 'device.timeout'] as const

export function classifySeverity(eventType: string): SeverityLevel {
  if (CRITICAL_PREFIXES.some((prefix) => eventType.startsWith(prefix))) return 'critical'
  if (WARNING_PREFIXES.some((prefix) => eventType.startsWith(prefix))) return 'warning'
  return 'info'
}

export const SEVERITY_LABELS: Record<SeverityLevel, string> = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
}
