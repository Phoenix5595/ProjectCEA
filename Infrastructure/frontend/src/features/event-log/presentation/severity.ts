import type { EventSeverity } from '../state/eventLogTypes'

export type SeverityLevel = EventSeverity

export const SEVERITY_LABELS: Record<SeverityLevel, string> = {
  critical: 'Critical',
  warning: 'Warning',
  error: 'Error',
  info: 'Info',
}
