import type { EventLogEntry } from '../state/eventLogStore'
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
  const relayState = entry.payload.state === true ? 'ON' : entry.payload.state === false ? 'OFF' : null
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
  if (setpointChange !== null) sourceParts.push({ text: setpointChange, className: 'font-semibold' })
  return sourceParts
}

function relayEngagedClass(entry: EventLogEntry): string | undefined {
  if (entry.category !== 'relay') return undefined
  const engaged = entry.payload.state === true || entry.payload.observed_state === true
  return engaged ? 'text-event-relay font-bold' : undefined
}

