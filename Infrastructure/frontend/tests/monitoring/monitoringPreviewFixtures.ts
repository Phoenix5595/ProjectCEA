/**
 * Route-table fixtures for the monitoring preview (`vite.monitoring.config.ts`):
 * sensor, control, relay-timeline, calendar and registry routes, plus the
 * scenario counters shared with the preview middleware.
 */
import { buildCalendarVisualFixture } from '../../src/components/calendar/calendarVisualFixture.ts'
import { eventHistoryFixture } from '../../src/features/event-log/config/fixtures.ts'
import {
  controlProjectionFixture,
  controlRangeFixture,
  controlTailFixture,
  parseBudget,
  parseRange,
  sensorLiveFixture,
  sensorRangeFixture,
} from '../../src/features/monitoring/config/fixtures.ts'

import {
  DASHBOARD_DEVICES,
  DASHBOARD_LIGHT_INTENSITIES,
  dashboardDeviceDetails,
  dashboardControlSnapshotFixture,
} from './dashboardDeviceFixtures.ts'
import type { FixtureReply } from './modeProfileFixtures'

interface FixtureRoute {
  re: RegExp
  handler: (
    req: { url?: string; method?: string; body?: string },
    scenario: string | null
  ) => unknown
}

export function roomFrom(url: string, index: number): string {
  return decodeURIComponent((url ?? '').split('?')[0].split('/').filter(Boolean)[index] ?? '')
}

export const scenarioCounters = new Map<string, number>()
export const MISSING_FIXTURE_SESSION = 'missing-session'

/** Deterministic grow-plan id carried by the calendar phase fixture rows (UUID form). */
const CALENDAR_FIXTURE_GROW_PLAN_ID = '3f2c9a1e-5d47-4b8e-9c6a-7ab90f43d2e1'

function nextLiveFixtureSequence(url: string, node: string): number {
  const query = url.split('?')[1] ?? ''
  const session = new URLSearchParams(query).get('fixtureSession') ?? MISSING_FIXTURE_SESSION
  const pathname = url.split('?')[0] ?? url
  const key = `live-update:${session}:${pathname}:${node}`
  const sequence = scenarioCounters.get(key) ?? 0
  scenarioCounters.set(key, sequence + 1)
  return sequence
}

const RELAY_TIMELINE_SESSION = 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a001'
interface RelayTimelineFixtureDevice {
  device_id: number
  device_name: string
  device_type: string
  display_name: string
  location: string
  cluster: string
  channel?: number
  relay_channel?: number
  physical_relay: number
  pid_enabled?: boolean
}

const RELAY_TIMELINE_DEVICES: RelayTimelineFixtureDevice[] = [
  {
    device_id: 101,
    device_name: 'veg_heater',
    device_type: 'heating',
    display_name: 'Veg Heater',
    location: 'Veg Room',
    cluster: 'main',
    channel: 0,
    physical_relay: 8,
    pid_enabled: false,
  },
  {
    device_id: 103,
    device_name: 'veg_co2',
    device_type: 'co2',
    display_name: 'CO₂',
    location: 'Veg Room',
    cluster: 'main',
    channel: 7,
    physical_relay: 3,
    pid_enabled: true,
  },
  {
    device_id: 102,
    device_name: 'veg_light',
    device_type: 'light',
    display_name: 'Grow Light',
    location: 'Veg Room',
    cluster: 'main',
    relay_channel: 1,
    physical_relay: 2,
  },
  {
    device_id: 201,
    device_name: 'flower_heater',
    device_type: 'heating',
    display_name: 'Flower Heater',
    location: 'Flower Room',
    cluster: 'main',
    channel: 2,
    physical_relay: 15,
    pid_enabled: false,
  },
  {
    device_id: 203,
    device_name: 'flower_co2',
    device_type: 'co2',
    display_name: 'Flower CO₂',
    location: 'Flower Room',
    cluster: 'main',
    channel: 8,
    physical_relay: 5,
    pid_enabled: true,
  },
  {
    device_id: 202,
    device_name: 'flower_light',
    device_type: 'light',
    display_name: 'Flower Light',
    location: 'Flower Room',
    cluster: 'main',
    relay_channel: 3,
    physical_relay: 4,
  },
]

function relayTimelineObservation(
  observationId: number,
  at: number,
  channel: number | null,
  state: boolean | null,
  reason: string,
  device: RelayTimelineFixtureDevice | null
): Record<string, unknown> {
  return {
    observation_id: observationId,
    time: new Date(at).toISOString(),
    channel,
    observed_state: state,
    reason,
    session_id: RELAY_TIMELINE_SESSION,
    registry_version: 1,
    device_id: device?.device_id ?? null,
    device_name: device?.device_name ?? null,
    device_type: device?.device_type ?? null,
    location: device?.location ?? null,
    cluster: device ? 'main' : null,
  }
}

function relayTimelineFixture(req: { url?: string }, _scenario: string | null): unknown {
  const { start, end } = parseRange(req.url ?? '')
  const startMs = Date.parse(start)
  const endMs = Date.parse(end)
  const room = roomFrom(req.url ?? '', 3)
  const roomDevices = RELAY_TIMELINE_DEVICES.filter(device => device.location === room)
  const heating =
    roomDevices.find(device => device.device_type === 'heating') ?? RELAY_TIMELINE_DEVICES[0]!
  const co2 = roomDevices.find(device => device.device_type === 'co2') ?? RELAY_TIMELINE_DEVICES[1]!
  const light =
    roomDevices.find(device => device.device_type === 'light') ?? RELAY_TIMELINE_DEVICES[2]!
  const heatingChannel = heating.channel ?? 0
  const co2Channel = co2.channel ?? 7
  const lightChannel = light.channel ?? light.relay_channel ?? 1
  const staleAt = startMs + 18 * 60_000
  const recoveredAt = startMs + 20 * 60_000
  const events: Array<Record<string, unknown>> = []
  const eventAt = (
    offsetMs: number,
    channel: number,
    state: boolean | null,
    reason: string,
    device: RelayTimelineFixtureDevice | null
  ): void => {
    const at = startMs + offsetMs
    if (at >= startMs && at < endMs)
      events.push(relayTimelineObservation(0, at, channel, state, reason, device))
  }

  eventAt(8 * 60_000, heatingChannel, false, 'state_changed', heating)
  eventAt(9 * 60_000, heatingChannel, true, 'state_changed', heating)
  eventAt(18 * 60_000, heatingChannel, null, 'stale', heating)
  eventAt(20 * 60_000, heatingChannel, false, 'recovered', heating)
  eventAt(25 * 60_000, heatingChannel, true, 'state_changed', heating)
  eventAt(27 * 60_000, heatingChannel, false, 'state_changed', heating)
  eventAt(28 * 60_000, heatingChannel, true, 'state_changed', heating)
  eventAt(10 * 60_000, co2Channel, true, 'state_changed', co2)
  eventAt(12 * 60_000, co2Channel, false, 'state_changed', co2)
  eventAt(6 * 60_000, lightChannel, true, 'state_changed', light)

  for (let at = startMs + 15_000; at < endMs; at += 30_000) {
    if (at >= staleAt && at < recoveredAt) continue
    events.push(relayTimelineObservation(0, at, null, null, 'heartbeat', null))
  }
  events.sort((a, b) => Date.parse(String(a.time)) - Date.parse(String(b.time)))
  const transitions = events.map((row, index) => ({ ...row, observation_id: 1_000 + index }))
  const anchors = [
    relayTimelineObservation(1, startMs - 40_000, heatingChannel, true, 'initial', heating),
    relayTimelineObservation(2, startMs - 40_000, co2Channel, false, 'initial', co2),
    relayTimelineObservation(3, startMs - 40_000, lightChannel, false, 'initial', light),
    relayTimelineObservation(4, startMs - 10_000, null, null, 'heartbeat', null),
  ]
  const query = new URLSearchParams((req.url ?? '').split('?')[1] ?? '')
  const cursor = query.get('cursor')
  const cursorOffset = cursor?.startsWith('fixture:') ? Number(cursor.slice('fixture:'.length)) : 0
  const offset = Number.isFinite(cursorOffset) && cursorOffset >= 0 ? Math.floor(cursorOffset) : 0
  const requestedLimit = Number(query.get('limit') ?? 2_000)
  const limit = Math.min(
    2_000,
    Math.max(1, Number.isFinite(requestedLimit) ? Math.floor(requestedLimit) : 2_000)
  )
  const page = transitions.slice(offset, offset + limit)
  const nextOffset = offset + page.length
  const hasMore = nextOffset < transitions.length
  const heartbeatTimes = transitions
    .filter(row => row.reason === 'heartbeat')
    .map(row => Date.parse(String(row.time)))
  const latestHeartbeat = heartbeatTimes.length > 0 ? Math.max(...heartbeatTimes) : startMs - 10_000
  const durationMs = endMs - startMs
  const aggregated = durationMs > 60 * 60_000
  const outputInterval = durationMs >= 24 * 60 * 60_000 ? 300 : aggregated ? 60 : 10
  const load = [
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      time: new Date(startMs + 20_000).toISOString(),
      requested_percent: 0,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      time: new Date(startMs + 50_000).toISOString(),
      requested_percent: 50,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      time: new Date(startMs + 80_000).toISOString(),
      requested_percent: null,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      time: new Date(startMs + 110_000).toISOString(),
      requested_percent: 100,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: co2.device_id,
      device_name: co2.device_name,
      time: new Date(startMs + 45_000).toISOString(),
      requested_percent: 25,
      aggregated,
      interval_seconds: outputInterval,
    },
  ].filter(point => Date.parse(point.time) >= startMs && Date.parse(point.time) < endMs)
  const coverageComplete = !(startMs < recoveredAt && endMs > staleAt)

  return {
    range: { start, end },
    transitions: page,
    anchors: offset === 0 ? anchors : [],
    load: offset === 0 ? load : [],
    coverage_complete: coverageComplete,
    last_heartbeat_at: new Date(latestHeartbeat).toISOString(),
    watermark: 1_000_000,
    has_more: hasMore,
    next_cursor: hasMore ? `fixture:${nextOffset}` : null,
  }
}

function relayTimelineRegistryFixture(): unknown {
  return RELAY_TIMELINE_DEVICES.map(device => ({
    ...device,
    inherited_schedule_count: 0,
    interlock_with: [],
    pid_setpoints: {},
  }))
}

function relayTimelineSnapshotFixture(): unknown {
  const assignmentsByChannel = new Map<number, RelayTimelineFixtureDevice>()
  for (const device of RELAY_TIMELINE_DEVICES) {
    const channel = device.channel ?? device.relay_channel
    if (channel !== undefined) assignmentsByChannel.set(channel, device)
  }
  const sampledAt = new Date().toISOString()
  return {
    generated_at: sampledAt,
    sampled_at: sampledAt,
    freshness: 'FRESH',
    registry_version: 1,
    stale_since: null,
    dfr_boards: [],
    failsafes: [],
    relays: Array.from({ length: 16 }, (_, channel) => {
      const device = assignmentsByChannel.get(channel)
      return {
        alarm: null,
        assignment: device
          ? {
              device_id: device.device_id,
              device_name: device.device_name,
              device_type: device.device_type,
              display_name: device.display_name,
              location: device.location,
              cluster: device.cluster,
              inherited_schedule_count: 0,
              inherited_schedule_summary: null,
            }
          : null,
        changed_at: sampledAt,
        channel,
        command_expires_at: null,
        command_mode: 'scheduled',
        desired_state: null,
        last_command_succeeded: null,
        interlock_blocked: false,
        interlock_reason: null,
        observed_state: device?.device_type === 'heating',
        physical_relay: device?.physical_relay ?? channel + 1,
        pin_label: `GPA${channel}`,
        prior_command_mode: null,
        recovery_pending: false,
        stale: false,
        syncing: false,
      }
    }),
    hardware_alarms: [],
  }
}

function relayTimelinePidFixture(req: { url?: string; method?: string; body?: string }): unknown {
  const pathParts = (req.url ?? '').split('?')[0].split('/').map(decodeURIComponent)
  const deviceType =
    pathParts[pathParts.length - (pathParts[pathParts.length - 1] === 'history' ? 2 : 1)] ??
    'heating'
  const location = pathParts[4] ?? 'Veg Room'
  const cluster = pathParts[5] ?? 'main'
  const bodyValue: unknown = req.method === 'POST' && req.body ? JSON.parse(req.body) : {}
  const body =
    typeof bodyValue === 'object' && bodyValue !== null
      ? (bodyValue as Record<string, unknown>)
      : {}
  if (pathParts[pathParts.length - 1] === 'history') {
    return [
      {
        location,
        cluster,
        device_type: deviceType,
        changed_at: '2026-09-25T12:00:00.000Z',
        kp: 1.4,
        ki: 0.25,
        kd: 0.08,
        binary_hysteresis: 0.1,
        source: 'fixture',
        updated_by: 'fixture operator',
      },
    ]
  }
  if ((req.url ?? '').includes('/api/pid/mode/')) {
    return {
      device_type: deviceType,
      mode: typeof body.mode === 'string' ? body.mode : 'pid',
      hysteresis_high: typeof body.hysteresis_high === 'number' ? body.hysteresis_high : 0.8,
      hysteresis_low: typeof body.hysteresis_low === 'number' ? body.hysteresis_low : 0.2,
      autotune_active: false,
      updated_at: '2026-09-25T12:00:00.000Z',
    }
  }
  return {
    kp: typeof body.kp === 'number' ? body.kp : 1.4,
    ki: typeof body.ki === 'number' ? body.ki : 0.25,
    kd: typeof body.kd === 'number' ? body.kd : 0.08,
    binary_hysteresis: 0.1,
    source: typeof body.source === 'string' ? body.source : 'fixture',
    updated_by: typeof body.updated_by === 'string' ? body.updated_by : 'fixture operator',
  }
}

/**
 * Mirror the monitoring-service history/tail scopes: `room` keeps climate
 * setpoints, lights and photoperiod; `climate` keeps climate and photoperiod;
 * `photoperiod` keeps only photoperiod; `all` (no scope) is unchanged.
 */
function scopedHistoryFixture(body: unknown, url: string): unknown {
  const scope = new URL(url, 'http://fixture.invalid').searchParams.get('scope') ?? 'all'
  if (scope === 'all') return body
  if (scope !== 'room' && scope !== 'climate' && scope !== 'photoperiod') {
    return { __status: 400, detail: "scope must be 'all', 'room', 'climate' or 'photoperiod'" }
  }
  const history = body as Record<string, unknown>
  return {
    ...history,
    climate: scope === 'photoperiod' ? [] : history.climate,
    lights: scope === 'room' ? history.lights : [],
    devices: [],
    pid: [],
    cursors: Array.isArray(history.cursors)
      ? history.cursors.filter(cursor =>
          typeof cursor !== 'object' || cursor === null ||
          !('source' in cursor) || cursor.source !== 'automation_state'
        )
      : history.cursors,
  }
}

/** Current-run reads derive their dates from the existing calendar fixture phases. */
export function calendarCurrentRunFixtureReply(
  req: { url?: string; method?: string },
  scenario: string | null
): FixtureReply | null {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  if (url.pathname !== '/api/calendar/current-run') return null
  if (req.method !== 'GET') return { status: 405, body: { detail: 'GET required' } }
  if (scenario === 'calendar-no-current-run') return { status: 200, body: null }
  if (scenario === 'calendar-ambiguous-current-run') {
    return { status: 409, body: { detail: 'Multiple current calendar grow plans' } }
  }
  const location = url.searchParams.get('location')
  const calendar = FIXTURE_ROUTES.find(route => route.re.test('/api/calendar/events'))!
  const data = calendar.handler({ url: '/api/calendar/events', method: 'GET' }, scenario) as {
    items: Array<{
      location: string
      grow_plan_id?: string
      event_type: string
      start_date: string
      end_date: string | null
      deleted_at: string | null
    }>
  }
  const phases: Record<string, true> = {
    clone_window: true, pot_veg: true, bed_veg: true, flip_to_flower: true,
    flower_stretch: true, flower_bulk: true, flower_ripen: true, drying: true, harvest: true,
  }
  const plans = new Map<string, { location: string; grow_plan_id: string; start_date: string; end_date: string }>()
  for (const event of data.items) {
    if (event.location !== location || !event.grow_plan_id || event.deleted_at ||
        phases[event.event_type] !== true) continue
    const end = event.end_date ?? event.start_date
    const plan = plans.get(event.grow_plan_id)
    if (plan) {
      if (event.start_date < plan.start_date) plan.start_date = event.start_date
      if (end > plan.end_date) plan.end_date = end
    } else {
      plans.set(event.grow_plan_id, {
        location: event.location, grow_plan_id: event.grow_plan_id,
        start_date: event.start_date, end_date: end,
      })
    }
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Toronto', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const dateParts = Object.fromEntries(parts.map(part => [part.type, part.value]))
  const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`
  const active = [...plans.values()].filter(plan => plan.start_date <= today && plan.end_date >= today)
  return active.length > 1
    ? { status: 409, body: { detail: 'Multiple current calendar grow plans' } }
    : { status: 200, body: active[0] ?? null }
}

export const FIXTURE_ROUTES: FixtureRoute[] = [
  {
    re: /^\/api\/sensors\/monitoring\/range\/([^/]+)/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return sensorRangeFixture(
        roomFrom(req.url ?? '', 4),
        start,
        end,
        scenario,
        parseBudget(req.url ?? '')
      )
    },
  },
  {
    re: /^\/api\/sensors\/monitoring\/live\/([^/]+)\/([^/]+)/,
    handler: (req, scenario) => {
      const parts = (req.url ?? '').split('?')[0].split('/').filter(Boolean)
      const node = decodeURIComponent(parts[parts.length - 1] ?? '')
      const sequence =
        scenario === 'live-update' || scenario === 'live-now'
          ? nextLiveFixtureSequence(req.url ?? '', node)
          : 0
      return sensorLiveFixture(node, scenario, sequence)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/relay-timeline$/,
    handler: (req, scenario) => relayTimelineFixture(req, scenario),
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/projection$/,
    handler: (req, scenario) => {
      const start = new Date().toISOString()
      const duration = scenario === 'nullable-projection' ? 30 * 60 * 1000 : 60 * 60 * 1000
      const end = new Date(Date.now() + duration).toISOString()
      return controlProjectionFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/tail$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return scopedHistoryFixture(
        controlTailFixture(roomFrom(req.url ?? '', 3), start, end, scenario),
        req.url ?? ''
      )
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/history$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return scopedHistoryFixture(
        controlRangeFixture(roomFrom(req.url ?? '', 3), start, end, scenario),
        req.url ?? ''
      )
    },
  },
  {
    re: /^\/api\/pid\/parameters\/[^/]+\/[^/]+\/[^/]+\/history$/,
    handler: req => relayTimelinePidFixture(req),
  },
  {
    re: /^\/api\/pid\/parameters\/[^/]+\/[^/]+\/[^/]+$/,
    handler: req => relayTimelinePidFixture(req),
  },
  {
    re: /^\/api\/pid\/mode\/[^/]+\/[^/]+\/[^/]+$/,
    handler: req => relayTimelinePidFixture(req),
  },
  {
    re: /^\/api\/events\/history$/,
    handler: (_req, scenario) => eventHistoryFixture(scenario),
  },
  {
    re: /^\/api\/calendar\/mode-schedule\//,
    handler: () => ({
      expected: { mode_name: 'flower', submode_name: null, title: 'Flowering' },
      active: { mode_name: 'flower', submode_name: null },
    }),
  },
  {
    re: /^\/api\/devices$/,
    handler: () => DASHBOARD_DEVICES,
  },
  {
    re: /^\/api\/devices\/([^/]+)\/([^/]+)$/,
    handler: req => {
      const location = decodeURIComponent(roomFrom(req.url ?? '', 2))
      const cluster = decodeURIComponent(roomFrom(req.url ?? '', 3))
      return {
        location,
        cluster,
        devices: dashboardDeviceDetails(location, cluster),
      }
    },
  },
  {
    re: /^\/api\/sensors\/([^/]+)\/([^/]+)\/live$/,
    handler: (req, scenario) => {
      const parts = (req.url ?? '').split('/').filter(Boolean)
      const location = decodeURIComponent(parts[2] ?? '')
      const cluster = decodeURIComponent(parts[3] ?? '')
      if (
        location !== 'Flower Room' ||
        (scenario !== 'dashboard-layout' && scenario !== 'disconnect')
      ) {
        const now = new Date().toISOString()
        const suffix = location === 'Flower Room' ? (cluster === 'front' ? '_f' : '_b') : '_v'
        const readings: Array<[string, number, string]> = location === 'Lab'
          ? [['lab_temp', 24.6, '°C'], ['water_temperature', 19.5, '°C']]
          : [[`dry_bulb${suffix}`, 24.5, '°C'], [`rh${suffix}`, 65, '%'],
            [`co2${suffix}`, 850, 'ppm'], [`vpd${suffix}`, 1.2, 'kPa']]
        return Object.fromEntries(readings.map(([sensor, value, unit]) => [
          sensor,
          { data: [{ time: now, timestamp: now, value }], unit, sensor_type: sensor, location, cluster },
        ]))
      }
      // Dashboard fixtures model stale front readings and live back readings.
      const now = new Date().toISOString()
      if (cluster === 'back') {
        return {
          dry_bulb_b: {
            sensor_type: 'dry_bulb_b',
            location,
            cluster,
            unit: '°C',
            data: [{ timestamp: now, value: 20.02 }],
          },
          wet_bulb_b: {
            sensor_type: 'wet_bulb_b',
            location,
            cluster,
            unit: '°C',
            data: [{ timestamp: now, value: 20.29 }],
          },
          rh_b: {
            sensor_type: 'rh_b',
            location,
            cluster,
            unit: '%',
            data: [{ timestamp: now, value: 92.98 }],
          },
          vpd_b: {
            sensor_type: 'vpd_b',
            location,
            cluster,
            unit: 'kPa',
            data: [{ timestamp: now, value: 0.12 }],
          },
          co2_b: {
            sensor_type: 'co2_b',
            location,
            cluster,
            unit: 'ppm',
            data: [{ timestamp: '2026-01-15T14:46:05.213000', value: 400.0 }],
          },
        }
      }
      const stale = '2026-01-26T18:14:12'
      return {
        dry_bulb_f: {
          sensor_type: 'dry_bulb_f',
          location,
          cluster,
          unit: '°C',
          data: [{ timestamp: stale, value: 16.75 }],
        },
        wet_bulb_f: {
          sensor_type: 'wet_bulb_f',
          location,
          cluster,
          unit: '°C',
          data: [{ timestamp: stale, value: 16.71 }],
        },
        rh_f: {
          sensor_type: 'rh_f',
          location,
          cluster,
          unit: '%',
          data: [{ timestamp: stale, value: 48.0 }],
        },
        co2_f: {
          sensor_type: 'co2_f',
          location,
          cluster,
          unit: 'ppm',
          data: [{ timestamp: stale, value: 400.0 }],
        },
        vpd_f: {
          sensor_type: 'vpd_f',
          location,
          cluster,
          unit: 'kPa',
          data: [{ timestamp: stale, value: 0.6 }],
        },
      }
    },
  },
  {
    re: /^\/api\/sensors\/live\/all$/,
    handler: () => [
      { sensor: 'Flower Room_front_dry_bulb_f', value: 24.5, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Flower Room_back_dry_bulb_b', value: 24.5, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Veg Room_main_dry_bulb_v', value: 23.8, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Lab_main_lab_temp', value: 24.6, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Lab_main_water_temperature', value: 19.5, time: new Date().toISOString(), unit: '°C' },
    ],
  },
  {
    re: /^\/api\/sensor-data$/,
    handler: req => {
      const values: Record<string, number> = {
        Lab_main_lab_temp: 24.6,
        Lab_main_water_temperature: 19.5,
        'Flower Room_main_heating_setpoint': 24,
        'Flower Room_main_cooling_setpoint': 27,
        'Flower Room_main_co2_setpoint': 900,
        'Flower Room_main_vpd_setpoint': 0.95,
        'Veg Room_main_heating_setpoint': 22,
        'Veg Room_main_cooling_setpoint': 26,
        'Veg Room_main_co2_setpoint': 800,
        'Veg Room_main_vpd_setpoint': 0.9,
        ...DASHBOARD_LIGHT_INTENSITIES,
      }
      const body = JSON.parse(req.body ?? '{}') as { keys?: string[] }
      return Object.fromEntries(
        (body.keys ?? []).filter(key => key in values).map(key => [key, values[key]])
      )
    },
  },
  {
    re: /^\/api\/status$/,
    handler: req => {
      const url = req.url ?? ''
      const isHealth = url.includes('health=true')
      if (isHealth) {
        return {
          service_health: [
            { name: 'automation-service', status: 'running', latency_ms: 12 },
            { name: 'cea-backend', status: 'running', latency_ms: 8 },
            { name: 'can-processor', status: 'stopped' },
          ],
        }
      }
      return {
        system: {
          cpu_percent: 15,
          memory_percent: 42,
          disk_percent: 28,
          uptime_seconds: 86400,
          load_avg: [0.5, 0.3, 0.2],
          process_count: 42,
          cpu_temp_c: 45,
          throttle_status: '0x0',
        },
        devices: {},
        degraded: null,
      }
    },
  },
  {
    re: /^\/api\/calendar\/flower-mode-transitions$/,
    handler: () => ({ enabled: true }),
  },
  {
    // The calendar-mode-change scenario owns this read in
    // calendarLifecycleFixtures; every other session has no expected harvest.
    re: /^\/api\/calendar\/expected-harvest\//,
    handler: () => null,
  },
  {
    re: /^\/api\/calendar\/events$/,
    handler: (req, scenario) => {
      const method = req.method ?? 'GET'
      const today = new Date()
      const plus = (n: number): string => {
        const d = new Date(today)
        d.setDate(d.getDate() + n)
        return d.toISOString().slice(0, 10)
      }
      if (method === 'GET' && scenario === 'calendar-visibility') {
        const room = new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get('location')
        return {
          items: buildCalendarVisualFixture(today)
            .filter(event => !room || event.location === room)
            .map((event, index) => ({
              ...event, numericId: event.source === 'manual' ? index + 100 : undefined,
              growPlanId: event.growPlanId ? CALENDAR_FIXTURE_GROW_PLAN_ID : undefined,
            })),
          next_cursor: null,
        }
      }
      if (method === 'POST') {
        const body = (
          typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body ?? {})
        ) as Record<string, unknown>
        return {
          id: 99,
          source: 'manual',
          event_type: body.event_type ?? 'planned_task',
          title: body.title ?? 'New event',
          start_date: body.start_date ?? plus(0),
          end_date: body.end_date ?? null,
          location: body.location ?? 'Flower Room',
          cluster: body.cluster ?? 'main',
          editable: true,
          notes: body.notes ?? null,
          deleted_at: null,
        }
      }
      if (method === 'PATCH') {
        const body = (
          typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body ?? {})
        ) as Record<string, unknown>
        return {
          id: 11,
          source: 'manual',
          event_type: body.event_type ?? 'planned_task',
          title: body.title ?? 'Updated event',
          start_date: body.start_date ?? plus(0),
          end_date: body.end_date ?? null,
          location: body.location ?? 'Flower Room',
          cluster: body.cluster ?? 'main',
          editable: true,
          notes: body.notes ?? null,
          deleted_at: null,
        }
      }
      return {
        items: [
          {
            id: 11,
            source: 'manual',
            event_type: 'planned_task',
            title: 'Reseed trays',
            start_date: plus(0),
            end_date: null,
            location: 'Flower Room',
            cluster: 'main',
            editable: true,
            notes: 'check domes after lights on',
            deleted_at: null,
          },
          {
            id: 12,
            source: 'manual',
            event_type: 'planned_task',
            title: 'Top dress Flower',
            start_date: plus(1),
            end_date: null,
            location: 'Flower Room',
            cluster: 'main',
            editable: true,
            notes: null,
            deleted_at: null,
          },
          {
            id: 13,
            source: 'mode_transition',
            event_type: 'flower_bulk',
            title: 'Flower bulk',
            start_date: plus(-5),
            end_date: plus(9),
            location: 'Flower Room',
            cluster: 'main',
            editable: false,
            notes: null,
            // Authoritative top-level plan UUID; metadata mirrors the real backend rows.
            grow_plan_id: CALENDAR_FIXTURE_GROW_PLAN_ID,
            metadata: { grow_plan_id: CALENDAR_FIXTURE_GROW_PLAN_ID },
            deleted_at: null,
          },
          {
            id: 13,
            source: 'mode_transition',
            event_type: 'flower_ripen',
            title: 'Flower ripen',
            start_date: plus(14),
            end_date: plus(28),
            location: 'Flower Room',
            cluster: 'main',
            editable: false,
            notes: null,
            grow_plan_id: CALENDAR_FIXTURE_GROW_PLAN_ID,
            metadata: { grow_plan_id: CALENDAR_FIXTURE_GROW_PLAN_ID },
            deleted_at: null,
          },
          {
            id: 14,
            source: 'manual',
            event_type: 'planned_task',
            title: 'Water transplant mix',
            start_date: plus(2),
            end_date: null,
            location: 'Veg Room',
            cluster: 'main',
            editable: true,
            notes: null,
            deleted_at: null,
          },
        ],
        next_cursor: null,
      }
    },
  },
  {
    re: /^\/api\/calendar\/events\/\d+$/,
    handler: req => {
      const body = (
        typeof req.body === 'string' ? JSON.parse(req.body || '{}') : (req.body ?? {})
      ) as Record<string, unknown>
      return {
        id: 11,
        source: 'manual',
        event_type: body.event_type ?? 'planned_task',
        title: body.title ?? 'Updated event',
        start_date: body.start_date ?? new Date().toISOString().slice(0, 10),
        end_date: body.end_date ?? null,
        location: body.location ?? 'Flower Room',
        cluster: body.cluster ?? 'main',
        editable: true,
        notes: body.notes ?? null,
        deleted_at: null,
      }
    },
  },
  {
    re: /^\/weather\/latest$/,
    handler: () => ({
      timestamp: new Date().toISOString(),
      data: {
        temp: { value: 18.4 },
        rh: { value: 62 },
        pressure: { value: 1013 },
        wind_speed: { value: 9.1 },
        wind_direction: { value: 240 },
        description: { value: 'Overcast' },
      },
    }),
  },
  {
    re: /^\/api\/devices\/control-snapshot$/,
    handler: (_req, scenario) =>
      scenario === 'relay-pid-timeline'
        ? relayTimelineSnapshotFixture()
        : dashboardControlSnapshotFixture(),
  },
  {
    re: /^\/api\/devices\/registry$/,
    handler: (_req, scenario) =>
      scenario === 'relay-pid-timeline' ? relayTimelineRegistryFixture() : DASHBOARD_DEVICES,
  },
]
