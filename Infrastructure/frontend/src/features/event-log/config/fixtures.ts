/**
 * Deterministic event-log fixtures for the monitoring preview harness.
 *
 * Generates operational events for the fixture preview server so Playwright
 * specs can exercise the event-log UI without contacting production. Each
 * scenario produces a stable set of events with known types, severities, and
 * rooms so tests can assert on exact content.
 */

interface OperationalEventEntity {
  entity_type: string
  entity_id: string
  location: string | null
  cluster: string | null
}

interface OperationalEvent {
  schema_version: 1
  event_id: string
  occurred_at: string
  source: 'automation' | 'api' | 'operator' | 'system' | 'transport'
  category: 'relay' | 'manual_override' | 'ramp' | 'control' | 'mutation' | 'alarm' | 'system'
  severity: 'info' | 'warning' | 'error' | 'critical'
  event_type: string
  correlation_id: string | null
  causation_id: string | null
  entity: OperationalEventEntity | null
  actor: { actor_type: 'operator' | 'service' | 'system'; actor_id: string | null } | null
  reason_code: string | null
  reason_text: string | null
  payload: Record<string, unknown>
}

interface OperationalEventItem {
  redis_id: string
  event: OperationalEvent
}

interface OperationalEventHistory {
  items: readonly OperationalEventItem[]
  newest_cursor: string | null
  oldest_cursor: string | null
  earliest_cursor: string | null
  has_more: boolean
  scan: { scanned: number; limit: number }
}

const BASE_TIME = new Date('2026-09-03T12:00:00.000Z').getTime()

function uuid(n: number): string {
  const hex = n.toString(16).padStart(12, '0')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4000-a000-${hex.padStart(12, '0')}`
}

function makeEvent(
  index: number,
  eventType: string,
  room: string,
  severity: 'info' | 'warning' | 'error' | 'critical',
  category: OperationalEvent['category'],
  extraPayload: Record<string, unknown> = {},
): OperationalEventItem {
  return {
    redis_id: `${BASE_TIME + index}-${index}`,
    event: {
      schema_version: 1,
      event_id: uuid(index),
      occurred_at: new Date(BASE_TIME + index * 1000).toISOString(),
      source: 'automation',
      category,
      severity,
      event_type: eventType,
      correlation_id: null,
      causation_id: null,
      entity: {
        entity_type: 'device',
        entity_id: `device-${index}`,
        location: room,
        cluster: 'main',
      },
      actor: { actor_type: 'system', actor_id: 'automation-service' },
      reason_code: null,
      reason_text: null,
      payload: { room, cluster: 'main', ...extraPayload },
    },
  }
}

const FLOWER_EVENTS: OperationalEventItem[] = [
  makeEvent(1, 'relay.state_changed', 'Flower Room', 'info', 'relay', { device_id: 'exhaust-fan', state: 'on' }),
  makeEvent(2, 'sensor.degraded', 'Flower Room', 'warning', 'system', { device_id: 'dry-bulb-front' }),
  makeEvent(3, 'system.failsafe_raised', 'Flower Room', 'critical', 'system', { reason: 'over-temperature' }),
  makeEvent(4, 'mode.transitioned', 'Flower Room', 'info', 'control', { old_mode: 'veg', new_mode: 'flower' }),
  makeEvent(5, 'config.updated', 'Flower Room', 'info', 'mutation', { setpoint: 25.5 }),
  makeEvent(16, 'alarm.triggered', 'Flower Room', 'critical', 'alarm', { reason: 'temperature-high' }),
  makeEvent(17, 'system.failsafe_raised', 'Flower Room', 'critical', 'system', { reason: 'humidity-critical' }),
  makeEvent(18, 'alarm.triggered', 'Flower Room', 'critical', 'alarm', { reason: 'co2-high' }),
]

const VEG_EVENTS: OperationalEventItem[] = [
  makeEvent(6, 'relay.command_issued', 'Veg Room', 'info', 'relay', { device_id: 'circulation-fan', state: 'on' }),
  makeEvent(7, 'device.timeout', 'Veg Room', 'warning', 'system', { device_id: 'soil-sensor-1' }),
  makeEvent(8, 'schedule.created', 'Veg Room', 'info', 'mutation', { mode_id: 'light-on' }),
  makeEvent(9, 'alarm.triggered', 'Veg Room', 'critical', 'alarm', { reason: 'humidity-low' }),
  makeEvent(10, 'setpoint.updated', 'Veg Room', 'info', 'mutation', { setpoint: 22.0 }),
]

const LAB_EVENTS: OperationalEventItem[] = [
  makeEvent(11, 'pid.parameters_changed', 'Lab', 'info', 'mutation', { device_id: 'heater-1' }),
  makeEvent(12, 'light.intensity_changed', 'Lab', 'info', 'mutation', { intensity: 75 }),
  makeEvent(13, 'device.registered', 'Lab', 'info', 'mutation', { device_id: 'new-sensor' }),
  makeEvent(14, 'notes.changed', 'Lab', 'info', 'mutation', { notes_changed: true, notes_length: 42 }),
  makeEvent(15, 'calendar.synced', 'Lab', 'info', 'mutation', { url_hostname: 'cal.example.com' }),
]

const ALL_EVENTS: OperationalEventItem[] = [...FLOWER_EVENTS, ...VEG_EVENTS, ...LAB_EVENTS]

const UNKNOWN_EVENT: OperationalEventItem = makeEvent(
  99,
  'custom.unknown_type_xyz',
  'Flower Room',
  'info',
  'system',
  { custom_field: 'test' },
)

const CJK_EVENT: OperationalEventItem = {
  redis_id: `${BASE_TIME + 100}-100`,
  event: {
    schema_version: 1,
    event_id: uuid(100),
    occurred_at: new Date(BASE_TIME + 100_000).toISOString(),
    source: 'system',
    category: 'system',
    severity: 'info',
    event_type: 'system.test_cjk',
    correlation_id: null,
    causation_id: null,
    entity: { entity_type: 'system', entity_id: 'test', location: 'Flower Room', cluster: 'main' },
    actor: { actor_type: 'system', actor_id: null },
    reason_code: null,
    reason_text: null,
    payload: {
      room: 'Flower Room',
      cluster: 'main',
      message_ko: '시스템 테스트 메시지',
      message_ja: 'システムテストメッセージ',
      message_zh: '系统测试消息',
    },
  },
}

export function eventHistoryFixture(scenario: string | null): OperationalEventHistory {
  if (scenario === 'empty') {
    return { items: [], newest_cursor: null, oldest_cursor: null, earliest_cursor: null, has_more: false, scan: { scanned: 0, limit: 500 } }
  }
  if (scenario === 'flower-only') {
    return { items: FLOWER_EVENTS, newest_cursor: FLOWER_EVENTS.at(-1)!.redis_id, oldest_cursor: FLOWER_EVENTS[0].redis_id, earliest_cursor: FLOWER_EVENTS[0].redis_id, has_more: false, scan: { scanned: FLOWER_EVENTS.length, limit: 500 } }
  }
  if (scenario === 'veg-only') {
    return { items: VEG_EVENTS, newest_cursor: VEG_EVENTS.at(-1)!.redis_id, oldest_cursor: VEG_EVENTS[0].redis_id, earliest_cursor: VEG_EVENTS[0].redis_id, has_more: false, scan: { scanned: VEG_EVENTS.length, limit: 500 } }
  }
  if (scenario === 'lab-only') {
    return { items: LAB_EVENTS, newest_cursor: LAB_EVENTS.at(-1)!.redis_id, oldest_cursor: LAB_EVENTS[0].redis_id, earliest_cursor: LAB_EVENTS[0].redis_id, has_more: false, scan: { scanned: LAB_EVENTS.length, limit: 500 } }
  }
  if (scenario === 'unknown-event') {
    return { items: [...FLOWER_EVENTS, UNKNOWN_EVENT], newest_cursor: UNKNOWN_EVENT.redis_id, oldest_cursor: FLOWER_EVENTS[0].redis_id, earliest_cursor: FLOWER_EVENTS[0].redis_id, has_more: false, scan: { scanned: FLOWER_EVENTS.length + 1, limit: 500 } }
  }
  if (scenario === 'cjk-payload') {
    return { items: [...FLOWER_EVENTS, CJK_EVENT], newest_cursor: CJK_EVENT.redis_id, oldest_cursor: FLOWER_EVENTS[0].redis_id, earliest_cursor: FLOWER_EVENTS[0].redis_id, has_more: false, scan: { scanned: FLOWER_EVENTS.length + 1, limit: 500 } }
  }
  if (scenario === 'cursor-trimmed') {
    return { items: [], newest_cursor: null, oldest_cursor: null, earliest_cursor: `${BASE_TIME + 50}-50`, has_more: false, scan: { scanned: 0, limit: 500 } }
  }
  return {
    items: ALL_EVENTS,
    newest_cursor: ALL_EVENTS.at(-1)!.redis_id,
    oldest_cursor: ALL_EVENTS[0].redis_id,
    earliest_cursor: ALL_EVENTS[0].redis_id,
    has_more: false,
    scan: { scanned: ALL_EVENTS.length, limit: 500 },
  }
}

export function sseFrameForEntry(entry: OperationalEventItem): string {
  return `id: ${entry.redis_id}\nevent: operational_event\ndata: ${JSON.stringify(entry.event)}\n\n`
}

export function sseHeartbeatFrame(): string {
  return `event: heartbeat\ndata: \n\n`
}

export function sseCursorFrame(cursor: string): string {
  return `id: ${cursor}\ndata: \n\n`
}

export { ALL_EVENTS, FLOWER_EVENTS, VEG_EVENTS, LAB_EVENTS, UNKNOWN_EVENT, CJK_EVENT }
