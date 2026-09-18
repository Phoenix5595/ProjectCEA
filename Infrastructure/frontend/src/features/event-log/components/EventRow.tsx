import { useState, useCallback, type KeyboardEvent } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import { isRelayActiveEvent, relayActiveStateClass } from '../presentation/categoryTheme'
import { formatRelativeTime, formatExactTime, formatLocalTime } from '../presentation/timeFormat'
import { formatSetpointFromTo } from '../presentation/setpointChange'
import { EventDetails } from './EventDetails'

interface EventRowProps {
  entry: EventLogEntry
  now: Date
  formatAbsolute?: (date: Date, now: Date) => string
}

const SEVERITY_VISUAL: Record<SeverityLevel, string> = {
  critical: 'border-l-[var(--status-danger-vivid)] border-l-4 bg-status-danger-bg/20',
  warning: 'border-l-status-warning bg-status-warning-bg/20',
  error: 'border-l-status-danger bg-surface-secondary',
  info: 'border-l-border-emphasis bg-surface-secondary',
}

const SEVERITY_BADGE: Record<SeverityLevel, string> = {
  critical: 'bg-status-danger-vivid text-status-danger-text border-status-danger-vivid font-bold',
  warning: 'bg-status-warning-bg text-status-warning-text border-status-warning-dim',
  error: 'bg-surface-secondary text-status-danger-text border-status-danger-border',
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

export function EventRow({ entry, now, formatAbsolute = formatLocalTime }: EventRowProps) {
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

  const sourceParts: Array<{ text: string; className?: string; title?: string }> = []
  if (entry.entity) {
    sourceParts.push({
      text: `${entityKindLabel(entry.entity.entityType)} ${entityShortName(entry.entity.entityId)}`,
      title: entry.entity.entityId,
    })
  }
  if (zone !== null) sourceParts.push({ text: zone })
  if (relayState !== null) {
    sourceParts.push({
      text: relayState,
      className: isRelayActiveEvent(entry) ? relayActiveStateClass(true) : undefined,
    })
  }
  if (controllerValue !== null) sourceParts.push({ text: `controller: ${controllerValue}` })
  if (deviceTypeValue !== null) sourceParts.push({ text: `device type: ${deviceTypeValue}` })
  if (missing !== null) sourceParts.push({ text: missing })
  const setpointChange = formatSetpointFromTo(entry.payload)
  if (setpointChange !== null) sourceParts.push({ text: setpointChange, className: 'font-semibold' })

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
          <div className="flex flex-col items-end">
            <time
              dateTime={entry.occurredAt.toISOString()}
              title={formatExactTime(entry.occurredAt)}
              className="shrink-0 text-[11px] text-text-default tabular-nums"
            >
              {formatRelativeTime(entry.occurredAt, now)}
            </time>
            <time
              dateTime={entry.occurredAt.toISOString()}
              aria-label={`Absolute time: ${formatAbsolute(entry.occurredAt, now)}`}
              className="shrink-0 text-[10px] text-text-secondary tabular-nums"
            >
              {formatAbsolute(entry.occurredAt, now)}
            </time>
          </div>
          </div>
          <div className="text-[11px] text-text-default font-mono truncate">{entry.type}</div>
          {sourceParts.length > 0 && (
            <div className="text-[11px] text-text-default truncate">
              {sourceParts.map((part, index) => (
                <span key={index} className={part.className} title={part.title}>
                  {part.text}
                  {index < sourceParts.length - 1 && ' \u00b7 '}
                </span>
              ))}
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
