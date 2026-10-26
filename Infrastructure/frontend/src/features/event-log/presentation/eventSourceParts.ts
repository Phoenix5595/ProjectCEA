import { formatTorontoClock } from '../../../utils/torontoWallTime'
import type { EventLogEntry } from '../state/eventLogStore'

import { formatPayloadValue } from './safePayload'
import { formatSetpointFromTo } from './setpointChange'

export interface EventSourcePart {
  text: string
  className?: string
  title?: string
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

/** Structured "at-a-glance" line parts for an event: entity, zone, state, controller, change values. */
export function sourcePartsFor(entry: EventLogEntry): EventSourcePart[] {
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
  const relayState =
    entry.payload.state === true ? 'ON' : entry.payload.state === false ? 'OFF' : null
  const missing = missingInputLabel(entry.type, deviceTypeValue)

  const sourceParts: EventSourcePart[] = []
  if (entry.entity) {
    sourceParts.push({
      text: `${entityKindLabel(entry.entity.entityType)} ${entityShortName(entry.entity.entityId)}`,
      title: entry.entity.entityId,
    })
  }
  if (zone !== null) sourceParts.push({ text: zone })
  if (relayState !== null) {
    sourceParts.push({ text: relayState, className: relayEngagedClass(entry) })
  }
  if (controllerValue !== null) sourceParts.push({ text: `controller: ${controllerValue}` })
  if (deviceTypeValue !== null) sourceParts.push({ text: `device type: ${deviceTypeValue}` })
  if (missing !== null) sourceParts.push({ text: missing })
  const setpointChange = formatSetpointFromTo(entry.payload)
  if (setpointChange !== null)
    sourceParts.push({ text: setpointChange, className: 'font-semibold' })
  return sourceParts
}

function relayEngagedClass(entry: EventLogEntry): string | undefined {
  if (entry.category !== 'relay') return undefined
  const engaged = entry.payload.state === true || entry.payload.observed_state === true
  return engaged ? 'text-event-relay font-bold' : undefined
}

function numberPayload(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function entityLabel(entry: EventLogEntry): string | null {
  if (entry.entity === null) return null
  return entry.entity.entityType === 'relay_channel'
    ? `Channel ${entry.entity.entityId}`
    : entityShortName(entry.entity.entityId)
}

function relayStateText(entry: EventLogEntry): string | null {
  const state = entry.payload.state
  if (typeof state === 'boolean') return state ? 'ON' : 'OFF'
  const observedState = entry.payload.observed_state
  if (typeof observedState === 'boolean') return observedState ? 'ON' : 'OFF'
  return null
}

function manualOverrideText(payload: Record<string, unknown>): string | null {
  const mode = stringPayload(payload.mode)
  if (mode === null) return null
  const expiresAt = stringPayload(payload.expires_at)
  if (expiresAt !== null) {
    const expiresAtMs = Date.parse(expiresAt)
    if (Number.isFinite(expiresAtMs)) return `${mode} until ${formatTorontoClock(expiresAtMs)}`
  }
  const durationSeconds = numberPayload(payload.duration_seconds)
  if (durationSeconds !== null) return `${mode} for ${Math.round(durationSeconds / 60)} min`
  return mode
}

function setpointText(payload: Record<string, unknown>): string | null {
  const effective = numberPayload(payload.effective_setpoint)
  if (effective === null) return null
  const previous = numberPayload(payload.previous_setpoint)
  return previous === null
    ? `SP ${formatPayloadValue(effective)}`
    : `SP ${formatPayloadValue(previous)}→${formatPayloadValue(effective)}`
}

function rampValuesText(payload: Record<string, unknown>): string | null {
  const target = numberPayload(payload.target_value)
  if (target === null) return null
  const start = numberPayload(payload.start_value)
  return start === null
    ? `→${formatPayloadValue(target)}`
    : `${formatPayloadValue(start)}→${formatPayloadValue(target)}`
}

function mutationChangeText(payload: Record<string, unknown>): string | null {
  const changes = payload.changes
  if (!Array.isArray(changes) || changes.length === 0) return null
  const first: unknown = changes[0]
  if (typeof first !== 'object' || first === null) return null
  const change = first as Record<string, unknown>
  const key = stringPayload(change.key)
  if (key === null) return null
  const text = `${key}: ${formatPayloadValue(change.before)}→${formatPayloadValue(change.after)}`
  return changes.length > 1 ? `${text} +${changes.length - 1} more` : text
}

/**
 * One-line detail for a dashboard category card: the latest event's salient
 * fields, joined with ` · `. Absent or wrongly typed source fields contribute
 * nothing; `—` stands in when no part is present.
 */
export function eventDetailLine(entry: EventLogEntry): string {
  const payload = entry.payload
  const room = stringPayload(payload.room)
  const parts: string[] = []

  if (entry.category === 'relay') {
    const label = entityLabel(entry)
    if (label !== null) parts.push(label)
    if (room !== null) parts.push(room)
    const state = relayStateText(entry)
    if (state !== null) parts.push(state)
  } else if (entry.category === 'control') {
    const controller = stringPayload(payload.device_type) ?? stringPayload(payload.controller)
    if (controller !== null) parts.push(controller)
    if (room !== null) parts.push(room)
    const setpoint = setpointText(payload)
    if (setpoint !== null) parts.push(setpoint)
  } else if (entry.category === 'ramp') {
    const rampType = stringPayload(payload.ramp_type)
    if (rampType !== null) parts.push(rampType)
    if (room !== null) parts.push(room)
    const values = rampValuesText(payload)
    if (values !== null) parts.push(values)
  } else if (entry.category === 'manual_override') {
    const label = entityLabel(entry)
    if (label !== null) parts.push(label)
    if (room !== null) parts.push(room)
    const override = manualOverrideText(payload)
    if (override !== null) parts.push(override)
  } else if (entry.category === 'mutation') {
    if (entry.entity !== null) {
      parts.push(`${entry.entity.entityType} ${entry.entity.entityId}`)
    }
    if (room !== null) parts.push(room)
    const change = mutationChangeText(payload)
    if (change !== null) parts.push(change)
  } else if (entry.category === 'alarm') {
    const code = stringPayload(payload.alarm_code)
    if (code !== null) parts.push(code)
    const state = stringPayload(payload.state)
    if (state !== null) parts.push(state)
    if (room !== null) parts.push(room)
  } else if (entry.category === 'system') {
    const component = stringPayload(payload.component)
    if (component !== null) parts.push(component)
    const state = stringPayload(payload.state)
    if (state !== null) parts.push(state)
  } else if (room !== null) {
    parts.push(room)
  }

  return parts.length === 0 ? '—' : parts.join(' · ')
}
