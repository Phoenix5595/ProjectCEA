import { useState, useCallback, type KeyboardEvent } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import { formatRelativeTime, formatExactTime } from '../presentation/timeFormat'
import { EventDetails } from './EventDetails'

interface EventRowProps {
  entry: EventLogEntry
  now: Date
}

const SEVERITY_VISUAL: Record<SeverityLevel, string> = {
  critical: 'border-l-status-danger bg-status-danger-bg/20',
  warning: 'border-l-status-warning bg-status-warning-bg/20',
  error: 'border-l-status-danger bg-status-danger-bg/20',
  info: 'border-l-border-emphasis bg-surface-secondary',
}

const SEVERITY_BADGE: Record<SeverityLevel, string> = {
  critical: 'bg-status-danger-bg text-status-danger-text border-status-danger-border',
  warning: 'bg-status-warning-bg text-status-warning-text border-status-warning-dim',
  error: 'bg-status-danger-bg text-status-danger-text border-status-danger-border',
  info: 'bg-surface-tertiary text-text-default border-border-default',
}

const ENTITY_KIND_LABELS: Record<string, string> = {
  device: 'Device',
  light: 'Light',
  relay_channel: 'Relay channel',
  relay_board: 'Relay board',
  room: 'Room',
  sensor: 'Sensor',
  system: 'System',
}

const SENSOR_INPUT_BY_DEVICE_TYPE: Record<string, string> = {
  heating: 'temperature',
  cooling: 'temperature',
  humidifier: 'humidity',
  dehumidifier: 'humidity',
  co2: 'CO₂',
}

function entityKindLabel(entityType: string): string {
  return ENTITY_KIND_LABELS[entityType] ?? entityType.replace(/_/g, ' ')
}

function entityShortName(entityId: string): string {
  const segments = entityId.split('/')
  return segments[segments.length - 1] || entityId
}

function stringPayload(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function missingInputLabel(type: string, deviceTypeValue: string | null): string | null {
  if (type !== 'control.input_missing' && type !== 'control.input_recovered') return null
  if (deviceTypeValue === null) return null
  const sensorKind = SENSOR_INPUT_BY_DEVICE_TYPE[deviceTypeValue]
  const inputName = sensorKind ? `${sensorKind} input` : `${deviceTypeValue} input`
  return type === 'control.input_missing' ? `no ${inputName}` : `${inputName} back`
}

export function EventRow({ entry, now }: EventRowProps) {
  const [expanded, setExpanded] = useState(false)
  const display = getEventDisplay(entry.type)
  const severity = entry.severity
  const toggle = useCallback(() => setExpanded((prev) => !prev), [])

  const roomValue = stringPayload(entry.payload.room)
  const clusterValue = stringPayload(entry.payload.cluster)
  const zone =
    roomValue !== null
      ? clusterValue !== null
        ? `${roomValue}/${clusterValue}`
        : roomValue
      : clusterValue
  const controllerValue = stringPayload(entry.payload.controller)
  const deviceTypeValue = stringPayload(entry.payload.device_type)
  const relayState = entry.payload.state === true ? 'ON' : entry.payload.state === false ? 'OFF' : null
  const missing = missingInputLabel(entry.type, deviceTypeValue)

  const sourceParts: string[] = []
  if (entry.entity) {
    sourceParts.push(
      `${entityKindLabel(entry.entity.entityType)} ${entityShortName(entry.entity.entityId)}`,
    )
  }
  if (zone !== null) sourceParts.push(zone)
  if (relayState !== null) sourceParts.push(relayState)
  if (controllerValue !== null) sourceParts.push(`controller: ${controllerValue}`)
  if (deviceTypeValue !== null) sourceParts.push(`device type: ${deviceTypeValue}`)
  if (missing !== null) sourceParts.push(missing)

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        toggle()
      }
    },
    [toggle],
  )

  return (
    <li role="listitem" className={`border-l-2 ${SEVERITY_VISUAL[severity]}`}>
      <div className="flex items-start gap-2 px-3 py-2">
        <span
          className={`shrink-0 inline-flex items-center px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider border ${SEVERITY_BADGE[severity]}`}
          aria-label={`Severity: ${SEVERITY_LABELS[severity]}`}
        >
          {SEVERITY_LABELS[severity]}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm text-text-default font-semibold truncate">{display.label}</span>
            <time
              dateTime={entry.occurredAt.toISOString()}
              title={formatExactTime(entry.occurredAt)}
              className="shrink-0 text-[11px] text-text-default tabular-nums"
            >
              {formatRelativeTime(entry.occurredAt, now)}
            </time>
          </div>
          <div className="text-[11px] text-text-default font-mono truncate">{entry.type}</div>
          {sourceParts.length > 0 && (
            <div className="text-[11px] text-text-default truncate" title={entry.entity?.entityId ?? undefined}>
              {sourceParts.join(' · ')}
            </div>
          )}
          {entry.reasonText !== null && (
            <div className="text-[11px] text-text-default italic truncate" title={entry.reasonText}>
              {entry.reasonText}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={toggle}
          onKeyDown={handleKeyDown}
          aria-expanded={expanded}
          aria-label="Toggle details"
          className="shrink-0 w-6 h-6 flex items-center justify-center text-text-default hover:text-text-default border border-border-subtle hover:border-border-default bg-surface-secondary transition-colors"
        >
          <span aria-hidden="true" className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>&#9654;</span>
        </button>
      </div>
      <EventDetails payload={entry.payload} expanded={expanded} />
    </li>
  )
}
