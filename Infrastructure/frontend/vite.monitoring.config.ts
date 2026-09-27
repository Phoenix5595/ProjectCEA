/**
 * Monitoring preview config (test-only).
 *
 * Serves the production `dist` build plus deterministic REST / WebSocket /
 * Grafana-placeholder / SPA-fallback fixtures on
 * `http://127.0.0.1:${MONITORING_FIXTURE_PORT ?? 4173}`. It injects a restrictive CSP and writes a request
 * log so exact-origin enforcement stays executable without Playwright route
 * interception (the same fixture endpoints are available to `/visual-qa`).
 *
 * This config is used by `playwright.monitoring.config.ts` and by the Vitest
 * browser harness test. It is NOT the dev server config.
 */
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import fs from 'node:fs'
import crypto from 'node:crypto'
import type { IncomingMessage } from 'node:http'
import { fileURLToPath } from 'node:url'
import {
  controlProjectionFixture,
  controlRangeFixture,
  controlTailFixture,
  grafanaPlaceholder,
  parseRange,
  sensorLiveFixture,
  sensorRangeFixture,
  sensorStatsFixture,
  wsFixtureMessage,
} from './src/features/monitoring/config/fixtures'
import { SOIL_FIXTURE_ROUTES } from './src/features/soil/config/soilFixtures'
import {
  eventHistoryFixture,
  sseFrameForEntry,
  sseHeartbeatFrame,
  sseCursorFrame,
  ALL_EVENTS,
  FLOWER_EVENTS,
  VEG_EVENTS,
  LAB_EVENTS,
  CJK_EVENT,
  GROUPED_CONSOLE_EVENTS,
} from './src/features/event-log/config/fixtures'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST_DIR = path.resolve(HERE, 'dist')

const FIXTURE_PORT = Number(process.env.MONITORING_FIXTURE_PORT ?? 4173)
const CSP =
  `default-src 'self'; connect-src 'self' ws://127.0.0.1:${FIXTURE_PORT}; ` +
  "frame-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'"

interface FixtureRoute {
  re: RegExp
  handler: (
    req: { url?: string; method?: string; body?: string },
    scenario: string | null
  ) => unknown
}

function roomFrom(url: string, index: number): string {
  return decodeURIComponent((url ?? '').split('/').filter(Boolean)[index] ?? '')
}

function scenarioFrom(url: string): string | null {
  const q = url.split('?')[1] ?? ''
  return new URLSearchParams(q).get('scenario')
}

function readRequestBody(req: IncomingMessage): Promise<string> {
  return new Promise(resolve => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer | string) => chunks.push(Buffer.from(chunk)))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', () => resolve(''))
  })
}

function isSensorPath(pathname: string): boolean {
  return pathname.startsWith('/api/sensors/monitoring/')
}

function isControlPath(pathname: string): boolean {
  return pathname.startsWith('/api/monitoring/control/')
}

function isDelayedHistoryPath(pathname: string): boolean {
  return /^\/api\/sensors\/monitoring\/range\//.test(pathname) || pathname.endsWith('/history')
}

const scenarioCounters = new Map<string, number>()
const MISSING_FIXTURE_SESSION = 'missing-session'

function nextLiveFixtureSequence(url: string, node: string): number {
  const query = url.split('?')[1] ?? ''
  const session = new URLSearchParams(query).get('fixtureSession') ?? MISSING_FIXTURE_SESSION
  const pathname = url.split('?')[0] ?? url
  const key = `live-update:${session}:${pathname}:${node}`
  const sequence = scenarioCounters.get(key) ?? 0
  scenarioCounters.set(key, sequence + 1)
  return sequence
}

function timelinePeriods(): unknown[] {
  return [
    {
      id: 17,
      period_name: 'Day cycle',
      start_time: '06:00:00',
      end_time: '18:00:00',
      ramp_minutes: 30,
      heating_setpoint: 24,
      cooling_setpoint: 28,
      vpd_setpoint: 1.1,
      co2_setpoint: 900,
      details: 'Fixture schedule',
    },
    {
      id: 18,
      period_name: 'Night cycle',
      start_time: '18:00:00',
      end_time: '06:00:00',
      ramp_minutes: 15,
      heating_setpoint: 19,
      cooling_setpoint: 24,
      vpd_setpoint: 0.8,
      co2_setpoint: 600,
      details: 'Fixture schedule',
    },
  ]
}

type TimelineEnvelopeOptions = {
  readonly baseConfigRevision?: string
  readonly draftRevision?: string | null
  readonly window?: { readonly start: string; readonly end: string; readonly timezone: string }
  readonly warnings?: readonly Record<string, string>[]
}

function timelineEnvelope(
  room: string,
  scope: 'saved' | 'draft',
  options: TimelineEnvelopeOptions = {}
): unknown {
  const baseConfigRevision = options.baseConfigRevision ?? '0000009'
  const draftRevision = scope === 'saved' ? null : (options.draftRevision ?? '1')
  const window = options.window ?? {
    start: '2026-08-02T00:00:00.000Z',
    end: '2026-08-03T00:00:00.000Z',
    timezone: 'UTC',
  }
  return {
    contract_version: 1,
    room,
    generated_at: '2026-08-02T12:00:00.000Z',
    window,
    revision_scope: scope,
    base_config_revision: baseConfigRevision,
    draft_revision: draftRevision,
    segments: [
      {
        shape: 'step',
        value: 24,
        start: '2026-08-02T06:00:00.000Z',
        end: '2026-08-02T18:00:00.000Z',
        metric: 'heating',
        unit: 'C',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'linear',
        start_value: 24,
        end_value: 22,
        start: '2026-08-02T18:00:00.000Z',
        end: '2026-08-02T18:20:00.000Z',
        metric: 'heating',
        unit: 'C',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'step',
        value: 22,
        start: '2026-08-02T18:20:00.000Z',
        end: '2026-08-03T06:00:00.000Z',
        metric: 'heating',
        unit: 'C',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'step',
        value: 23,
        start: '2026-08-02T06:00:00.000Z',
        end: '2026-08-03T06:00:00.000Z',
        metric: 'heating',
        unit: 'C',
        trajectory_kind: 'effective',
        quality: 'estimated',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'step',
        value: 25,
        start: '2026-08-02T00:00:00.000Z',
        end: '2026-08-03T00:00:00.000Z',
        metric: 'cooling_setpoint',
        unit: 'C',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'step',
        value: 1.1,
        start: '2026-08-02T00:00:00.000Z',
        end: '2026-08-03T00:00:00.000Z',
        metric: 'vpd_setpoint',
        unit: 'kPa',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
      {
        shape: 'step',
        value: 900,
        start: '2026-08-02T00:00:00.000Z',
        end: '2026-08-03T00:00:00.000Z',
        metric: 'co2_setpoint',
        unit: 'ppm',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: '1',
          submode: null,
          period: { period_id: '17', label: 'Day cycle' },
          config_revision: baseConfigRevision,
          draft_revision: draftRevision,
        },
      },
    ],
    assumptions: ['Fixture trajectory is a saved schedule authority.'],
    warnings: options.warnings ?? [],
  }
}

function timelineFixture(
  req: { url?: string; method?: string; body?: string },
  scenario: string | null
): unknown {
  const room =
    (req.url ?? '').includes('Veg%20Room') || (req.url ?? '').includes('Veg Room')
      ? 'Veg Room'
      : 'Flower Room'
  const periods = timelinePeriods()
  const photoperiod = {
    day_start_time: '06:00:00',
    night_start_time: '18:00:00',
    ramp_up_minutes: 20,
    ramp_down_minutes: 20,
  }
  const warnings =
    scenario === 'calendar-transition-skipped'
      ? [
          {
            code: 'calendar.transition_skipped',
            detail: 'Calendar destination flower/bulk was skipped',
            reason: 'unknown_mode',
            start: '2026-08-02T06:00:00.000Z',
            end: '2026-08-02T12:00:00.000Z',
          },
        ]
      : []
  if (req.method === 'POST' && (req.url ?? '').endsWith('/preview')) {
    const request = JSON.parse(req.body ?? '{}') as {
      request_id?: string
      expected_config_revision?: string
      draft_revision?: number
      window?: { start: string; end: string; timezone: string }
    }
    const previewRevision =
      scenario === 'timeline-preview-stale'
        ? 'stale-config-revision'
        : (request.expected_config_revision ?? '0000009')
    const previewDraftRevision =
      scenario === 'timeline-preview-stale'
        ? String((request.draft_revision ?? 1) + 1)
        : String(request.draft_revision ?? 1)
    const previewRoom = scenario === 'timeline-wrong-room' ? 'Veg Room' : room
    return {
      request_id: request.request_id ?? 'fixture-request',
      expected_config_revision: request.expected_config_revision ?? '0000009',
      draft_revision: request.draft_revision ?? 1,
      trajectory: timelineEnvelope(previewRoom, 'draft', {
        baseConfigRevision: previewRevision,
        draftRevision: previewDraftRevision,
        warnings,
        ...(request.window === undefined ? {} : { window: request.window }),
      }),
    }
  }
  if (req.method === 'POST' && (req.url ?? '').endsWith('/apply')) {
    return {
      request_id: 'fixture-request',
      config_revision: '000000a',
      mode_id: 1,
      submode_id: null,
      periods,
      photoperiod,
    }
  }
  return {
    config_revision: '0000009',
    mode_id: 1,
    submode_id: null,
    periods,
    photoperiod,
    trajectory: timelineEnvelope(room, 'saved', { warnings }),
  }
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
    observed_at: new Date(at).toISOString(),
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
  events.sort((a, b) => Date.parse(String(a.observed_at)) - Date.parse(String(b.observed_at)))
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
    .map(row => Date.parse(String(row.observed_at)))
  const latestHeartbeat = heartbeatTimes.length > 0 ? Math.max(...heartbeatTimes) : startMs - 10_000
  const durationMs = endMs - startMs
  const aggregated = durationMs > 60 * 60_000
  const outputInterval = durationMs >= 24 * 60 * 60_000 ? 300 : aggregated ? 60 : 10
  const load = [
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      timestamp: new Date(startMs + 20_000).toISOString(),
      requested_percent: 0,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      timestamp: new Date(startMs + 50_000).toISOString(),
      requested_percent: 50,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      timestamp: new Date(startMs + 80_000).toISOString(),
      requested_percent: null,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: heating.device_id,
      device_name: heating.device_name,
      timestamp: new Date(startMs + 110_000).toISOString(),
      requested_percent: 100,
      aggregated,
      interval_seconds: outputInterval,
    },
    {
      device_id: co2.device_id,
      device_name: co2.device_name,
      timestamp: new Date(startMs + 45_000).toISOString(),
      requested_percent: 25,
      aggregated,
      interval_seconds: outputInterval,
    },
  ].filter(point => Date.parse(point.timestamp) >= startMs && Date.parse(point.timestamp) < endMs)
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

const FIXTURE_ROUTES: FixtureRoute[] = [
  ...SOIL_FIXTURE_ROUTES,
  {
    re: /^\/api\/sensors\/monitoring\/range\/([^/]+)/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return sensorRangeFixture(roomFrom(req.url ?? '', 4), start, end, scenario)
    },
  },
  {
    re: /^\/api\/sensors\/monitoring\/live\/([^/]+)\/([^/]+)/,
    handler: (req, scenario) => {
      const parts = (req.url ?? '').split('?')[0].split('/').filter(Boolean)
      const node = decodeURIComponent(parts[parts.length - 1] ?? '')
      const sequence = scenario === 'live-update' ? nextLiveFixtureSequence(req.url ?? '', node) : 0
      return sensorLiveFixture(node, scenario, sequence)
    },
  },
  {
    re: /^\/api\/sensors\/monitoring\/stats\/([^/]+)/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return sensorStatsFixture(roomFrom(req.url ?? '', 4), start, end, scenario)
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
      return controlTailFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
    },
  },
  {
    re: /^\/api\/monitoring\/control\/([^/]+)\/history$/,
    handler: (req, scenario) => {
      const { start, end } = parseRange(req.url ?? '')
      return controlRangeFixture(roomFrom(req.url ?? '', 3), start, end, scenario)
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
    re: /^\/grafana\//,
    handler: () => grafanaPlaceholder(),
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
    re: /^\/api\/room-modes\/active\/[^/]+\/[^/]+$/,
    handler: (req) => {
      const parts = new URL(req.url ?? '', 'http://fixture.invalid').pathname
        .split('/')
        .filter(Boolean)
      const location = decodeURIComponent(parts[3] ?? '')
      const cluster = decodeURIComponent(parts[4] ?? '')
      return {
        location,
        cluster,
        mode_name: location === 'Flower Room' ? 'sleep' : 'veg',
        submode_name: null,
        mode_id: 1,
        submode_id: null,
      }
    },
  },
  {
    re: /^\/api\/room-modes\/room\/[^/]+\/[^/]+$/,
    handler: (req, scenario) => ({
      location: (req.url ?? '').includes('Veg%20Room') ? 'Veg Room' : 'Flower Room',
      cluster: 'main',
      mode_name:
        scenario === 'sleep-scheduled-flag'
          ? 'sleep'
          : (req.url ?? '').includes('Veg%20Room')
            ? 'veg'
            : 'flower',
      mode_id: 1,
      submode_id: null,
      is_constant: scenario === 'veg-constant-flag',
      parameters: {
        day_start_time: '06:00',
        night_start_time: '18:00',
        light_ramp_up_minutes: 20,
        light_ramp_down_minutes: 20,
        main_light_intensity: 80,
        supplemental_light_intensity: 10,
      },
    }),
  },
  {
    re: /^\/api\/room-modes\/room\/[^/]+\/[^/]+\/parameters$/,
    handler: (req, scenario) => ({
      location: (req.url ?? '').includes('Veg%20Room') ? 'Veg Room' : 'Flower Room',
      cluster: 'main',
      mode_name: scenario === 'sleep-scheduled-flag' ? 'sleep' : 'flower',
      mode_id: 1,
      submode_id: null,
      parameters: {
        day_start_time: '06:00',
        night_start_time: '18:00',
        light_ramp_up_minutes: 20,
        light_ramp_down_minutes: 20,
        main_light_intensity: 80,
        supplemental_light_intensity: 10,
      },
    }),
  },
  {
    re: /^\/api\/climate-periods\/[^/]+\/[^/]+$/,
    handler: () => timelinePeriods(),
  },
  {
    re: /^\/api\/climate-timeline\/[^/]+\/[^/]+(?:\/preview|\/apply)?$/,
    handler: (req, scenario) => timelineFixture(req, scenario),
  },
  {
    re: /^\/api\/devices$/,
    handler: () => [
      {
        location: 'Flower Room',
        cluster: 'main',
        device_name: 'exhaust-fan',
        state: 1,
        mode: 'auto',
        channel: 1,
      },
      {
        location: 'Flower Room',
        cluster: 'main',
        device_name: 'circulation-fan',
        state: 1,
        mode: 'auto',
        channel: 2,
      },
      {
        location: 'Veg Room',
        cluster: 'main',
        device_name: 'circulation-fan',
        state: 1,
        mode: 'auto',
        channel: 3,
      },
      {
        location: 'Lab',
        cluster: 'main',
        device_name: 'heater-1',
        state: 0,
        mode: 'auto',
        channel: 4,
      },
    ],
  },
  {
    re: /^\/api\/devices\/([^/]+)\/([^/]+)$/,
    handler: req => {
      const location = decodeURIComponent(roomFrom(req.url ?? '', 2))
      const cluster = decodeURIComponent(roomFrom(req.url ?? '', 3))
      return {
        location,
        cluster,
        devices: {
          'exhaust-fan': {
            device_type: 'fan',
            display_name: 'Exhaust Fan',
            mode: 'auto',
            state: 1,
            channel: 1,
          },
          'circulation-fan': {
            device_type: 'fan',
            display_name: 'Circulation Fan',
            mode: 'auto',
            state: 1,
            channel: 2,
          },
        },
      }
    },
  },
  {
    re: /^\/api\/sensors\/([^/]+)\/([^/]+)\/live$/,
    handler: (req, scenario) => {
      const parts = (req.url ?? '').split('/').filter(Boolean)
      const location = decodeURIComponent(parts[2] ?? '')
      const cluster = decodeURIComponent(parts[3] ?? '')
      if (scenario !== 'dashboard-layout' && scenario !== 'disconnect') {
        const now = new Date().toISOString()
        return {
          temperature: { data: [{ time: now, timestamp: now, value: 24.5 }], unit: '°C', sensor_name: 'temperature' },
          humidity: { data: [{ time: now, timestamp: now, value: 65 }], unit: '%', sensor_name: 'humidity' },
          co2: { data: [{ time: now, timestamp: now, value: 850 }], unit: 'ppm', sensor_name: 'co2' },
          vpd: { data: [{ time: now, timestamp: now, value: 1.2 }], unit: 'kPa', sensor_name: 'vpd' },
        }
      }
      // Dashboard fixtures model stale front readings and live back readings.
      if (location !== 'Flower Room') return {}
      const now = new Date().toISOString()
      if (cluster === 'back') {
        return {
          dry_bulb_b: { sensor_type: 'dry_bulb_b', location, cluster, unit: '°C', data: [{ timestamp: now, value: 20.02 }] },
          wet_bulb_b: { sensor_type: 'wet_bulb_b', location, cluster, unit: '°C', data: [{ timestamp: now, value: 20.29 }] },
          rh_b: { sensor_type: 'rh_b', location, cluster, unit: '%', data: [{ timestamp: now, value: 92.98 }] },
          vpd_b: { sensor_type: 'vpd_b', location, cluster, unit: 'kPa', data: [{ timestamp: now, value: 0.12 }] },
          co2_b: { sensor_type: 'co2_b', location, cluster, unit: 'ppm', data: [{ timestamp: '2026-01-15T14:46:05.213000', value: 400.0 }] },
        }
      }
      const stale = '2026-01-26T18:14:12'
      return {
        dry_bulb_f: { sensor_type: 'dry_bulb_f', location, cluster, unit: '°C', data: [{ timestamp: stale, value: 16.75 }] },
        wet_bulb_f: { sensor_type: 'wet_bulb_f', location, cluster, unit: '°C', data: [{ timestamp: stale, value: 16.71 }] },
        rh_f: { sensor_type: 'rh_f', location, cluster, unit: '%', data: [{ timestamp: stale, value: 48.0 }] },
        co2_f: { sensor_type: 'co2_f', location, cluster, unit: 'ppm', data: [{ timestamp: stale, value: 400.0 }] },
        vpd_f: { sensor_type: 'vpd_f', location, cluster, unit: 'kPa', data: [{ timestamp: stale, value: 0.6 }] },
      }
    },
  },
  {
    re: /^\/api\/sensors\/live\/all$/,
    handler: () => [
      {
        sensor: 'Flower Room_main_temperature',
        value: 24.5,
        time: new Date().toISOString(),
        unit: '°C',
      },
      { sensor: 'Flower Room_main_humidity', value: 65, time: new Date().toISOString(), unit: '%' },
      {
        sensor: 'Veg Room_main_temperature',
        value: 23.8,
        time: new Date().toISOString(),
        unit: '°C',
      },
      { sensor: 'Veg Room_main_humidity', value: 68, time: new Date().toISOString(), unit: '%' },
      { sensor: 'Lab_main_temperature', value: 22.1, time: new Date().toISOString(), unit: '°C' },
      { sensor: 'Lab_main_humidity', value: 55, time: new Date().toISOString(), unit: '%' },
    ],
  },
  {
    re: /^\/api\/sensor-data$/,
    handler: () => ({
      'Flower Room_main_temperature': 24.5,
      'Flower Room_main_humidity': 65,
      'Veg Room_main_temperature': 23.8,
      'Veg Room_main_humidity': 68,
      'Lab_main_temperature': 22.1,
      'Lab_main_humidity': 55,
      'Lab_main_lab_temp': 24.6,
      'Lab_main_water_temperature': 19.5,
      'Flower Room_main_heating_setpoint': 24,
      'Flower Room_main_cooling_setpoint': 27,
      'Flower Room_main_co2_setpoint': 900,
      'Flower Room_main_vpd_setpoint': 0.95,
      'Veg Room_main_heating_setpoint': 22,
      'Veg Room_main_cooling_setpoint': 26,
      'Veg Room_main_co2_setpoint': 800,
      'Veg Room_main_vpd_setpoint': 0.9,
      'Flower Room_main_light_1_intensity': 55,
      'Flower Room_main_light_2_intensity': 48,
      'Flower Room_main_light_3_intensity': 60,
      'Veg Room_main_light_1_intensity': 40,
      'Veg Room_main_light_2_intensity': 35,
      'Veg Room_main_light_3_intensity': 42,
    }),
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
    re: /^\/api\/calendar\/events$/,
    handler: (req) => {
      const method = req.method ?? 'GET'
      const today = new Date()
      const plus = (n: number): string => {
        const d = new Date(today)
        d.setDate(d.getDate() + n)
        return d.toISOString().slice(0, 10)
      }
      if (method === 'POST') {
        const body = (typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body ?? {}) as Record<string, unknown>
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
        const body = (typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body ?? {}) as Record<string, unknown>
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
            metadata: { grow_plan_id: 'gp-fixture' },
            deleted_at: null,
          },
          {
            id: 14,
            source: 'mode_transition',
            event_type: 'flower_ripen',
            title: 'Flower ripen',
            start_date: plus(14),
            end_date: plus(28),
            location: 'Flower Room',
            cluster: 'main',
            editable: false,
            notes: null,
            metadata: { grow_plan_id: 'gp-fixture' },
            deleted_at: null,
          },
          {
            id: 15,
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
    handler: (req) => {
      const body = (typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body ?? {}) as Record<string, unknown>
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
        : {
            generated_at: new Date().toISOString(),
            sampled_at: new Date().toISOString(),
            freshness: 'FRESH',
            registry_version: 1,
            stale_since: null,
            dfr_boards: [],
            relays: Array.from({ length: 16 }, (_, channel) => ({
              alarm: null,
              assignment: null,
              changed_at: null,
              channel,
              command_expires_at: null,
              command_mode: 'scheduled',
              desired_state: null,
              last_command_succeeded: null,
              observed_state: false,
              physical_relay: channel + 1,
              pin_label: `GPA${channel}`,
              prior_command_mode: null,
              recovery_pending: false,
              stale: false,
              syncing: false,
            })),
            hardware_alarms: [],
          },
  },
  {
    re: /^\/api\/devices\/registry$/,
    handler: (_req, scenario) =>
      scenario === 'relay-pid-timeline' ? relayTimelineRegistryFixture() : [],
  },
]

function wsAccept(key: string): string {
  const digest = crypto
    .createHash('sha1')
    .update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
    .digest('base64')
  return digest
}

/** Encode a server->client text frame (unmasked). */
function encodeTextFrame(payload: Buffer): Buffer {
  const len = payload.length
  let header: Buffer
  if (len < 126) {
    header = Buffer.from([0x81, len])
  } else if (len < 65536) {
    header = Buffer.from([0x81, 126, (len >> 8) & 0xff, len & 0xff])
  } else {
    header = Buffer.alloc(10)
    header[0] = 0x81
    header[1] = 127
    header.writeBigUInt64BE(BigInt(len), 2)
  }
  return Buffer.concat([header, payload])
}

function monitoringPreviewPlugin(): Plugin {
  return {
    name: 'monitoring-preview-fixtures',
    configurePreviewServer(server) {
      const logPath = process.env.MONITORING_PREVIEW_LOG
      const log = (line: string): void => {
        if (logPath) {
          try {
            fs.appendFileSync(logPath, `${line}\n`)
          } catch {
            /* logging must never break the preview */
          }
        }
      }

      server.middlewares.use(async (req, res, next) => {
        log(`REQUEST ${req.method} ${req.url}`)
        res.setHeader('Content-Security-Policy', CSP)
        const pathname = (req.url ?? '/').split('?')[0]
        const isFixtureMutation =
          (req.method === 'POST' &&
            (/^\/api\/climate-timeline\//.test(pathname) ||
              /^\/api\/pid\/(?:parameters|mode)\//.test(pathname) ||
              /^\/api\/calendar\/events/.test(pathname))) ||
          (req.method === 'PATCH' && /^\/api\/calendar\/events/.test(pathname))
        const requestBody = isFixtureMutation ? await readRequestBody(req) : undefined
        const scenario = scenarioFrom(req.url ?? '') ?? scenarioFrom(req.headers.referer ?? '')
        const fixtureSession =
          new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get('fixtureSession') ??
          MISSING_FIXTURE_SESSION
        const counterKey = (name: string): string => `${name}:${fixtureSession}:${pathname}`

        if (scenario === 'backend-down' && isSensorPath(pathname)) {
          const key = `backend-down:${fixtureSession}`
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 3) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'backend down (fixture)' }))
            return
          }
        }
        if (scenario === 'automation-down' && isControlPath(pathname)) {
          const key = `automation-down:${fixtureSession}`
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 3) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'automation down (fixture)' }))
            return
          }
        }
        if (
          scenario === 'timeline-api-failure' &&
          req.method === 'GET' &&
          /^\/api\/climate-timeline\//.test(pathname)
        ) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'saved timeline unavailable (fixture)' }))
          return
        }
        if (
          scenario === 'timeline-unavailable-409' &&
          req.method === 'GET' &&
          /^\/api\/climate-timeline\//.test(pathname)
        ) {
          res.statusCode = 409
          res.setHeader('Content-Type', 'application/json')
          res.end(
            JSON.stringify({
              detail: {
                code: 'timeline_unavailable',
                detail: 'saved timeline unavailable (fixture)',
              },
            })
          )
          return
        }
        if (
          scenario === 'timeline-preview-failed' &&
          req.method === 'POST' &&
          pathname.endsWith('/preview')
        ) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'preview unavailable (fixture)' }))
          return
        }
        if (
          scenario === 'timeline-apply-conflict' &&
          req.method === 'POST' &&
          pathname.endsWith('/apply')
        ) {
          res.statusCode = 409
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'saved revision changed (fixture)' }))
          return
        }
        if (scenario === 'force-error' && isSensorPath(pathname)) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'forced monitoring error (fixture)' }))
          return
        }
        if (scenario === 'control-history-transient' && pathname.endsWith('/history')) {
          const key = counterKey('control-history-transient')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 2) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'control history unavailable (fixture)' }))
            return
          }
        }
        if (scenario === 'control-history-persistent' && pathname.endsWith('/history')) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'control history persistently unavailable (fixture)' }))
          return
        }
        if (scenario === 'fixed-range-retry' && pathname.endsWith('/history')) {
          const { start } = parseRange(req.url ?? '')
          if (start === '2026-08-02T16:00:00.000Z') {
            const key = counterKey('fixed-range-retry')
            const count = scenarioCounters.get(key) ?? 0
            scenarioCounters.set(key, count + 1)
            if (count === 0) {
              res.statusCode = 503
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ detail: 'fixed control history unavailable (fixture)' }))
              return
            }
          }
        }
        if (
          scenario === 'range-503-after-good' &&
          /^\/api\/sensors\/monitoring\/range\//.test(pathname)
        ) {
          const key = counterKey('range-503-after-good')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count === 1) {
            res.statusCode = 503
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'range unavailable (fixture)' }))
            return
          }
        }
        if (
          scenario === 'malformed-sensor' &&
          /^\/api\/sensors\/monitoring\/range\//.test(pathname)
        ) {
          const key = counterKey('malformed-sensor')
          const count = scenarioCounters.get(key) ?? 0
          scenarioCounters.set(key, count + 1)
          if (count < 2) {
            const { start, end } = parseRange(req.url ?? '')
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                metadata: {
                  generated_at: '2026-08-02T12:00:00.000Z',
                  tier: 'raw',
                  range: { start, end },
                  room: { room: roomFrom(req.url ?? '', 4), nodes: ['front', 'back'] },
                },
                series: [
                  {
                    sensor: 'dry_bulb_f',
                    node: 'front',
                    unit_family: 'celsius',
                    unit: '°C',
                    points: [
                      {
                        timestamp: start,
                        average: 'not-a-number',
                        minimum: 24.1,
                        maximum: 24.9,
                        sample_count: 60,
                      },
                    ],
                  },
                ],
                statistics: [],
              })
            )
            return
          }
        }

        // Event-log scenario handling
        if (pathname === '/api/events/history') {
          if (scenario === 'auth-401') {
            res.statusCode = 401
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'unauthorized (fixture)' }))
            return
          }
          if (scenario === 'error-500') {
            res.statusCode = 500
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'internal error (fixture)' }))
            return
          }
          if (scenario === 'cursor-trimmed-409') {
            res.statusCode = 409
            res.setHeader('Content-Type', 'application/json')
            res.end(
              JSON.stringify({
                earliest_cursor: `${Date.now() - 60000}-0`,
                latest_cursor: `${Date.now()}-999`,
              })
            )
            return
          }
        }

        // SSE stream endpoint
        if (pathname === '/api/events/stream') {
          if (scenario === 'auth-401') {
            res.statusCode = 401
            res.setHeader('Content-Type', 'application/json')
            res.end(JSON.stringify({ detail: 'unauthorized (fixture)' }))
            return
          }
          if (scenario === 'disconnect') {
            res.statusCode = 200
            res.setHeader('Content-Type', 'text/event-stream')
            res.setHeader('Cache-Control', 'no-cache')
            res.setHeader('Connection', 'keep-alive')
            res.end()
            return
          }
          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream')
          res.setHeader('Cache-Control', 'no-cache')
          res.setHeader('Connection', 'keep-alive')

          // Send heartbeat first
          res.write(sseHeartbeatFrame())

          // Select events based on scenario
          let eventsToSend = ALL_EVENTS
          if (scenario === 'flower-only') eventsToSend = FLOWER_EVENTS
          else if (scenario === 'veg-only') eventsToSend = VEG_EVENTS
          else if (scenario === 'lab-only') eventsToSend = LAB_EVENTS
          else if (scenario === 'cjk-payload') eventsToSend = [...FLOWER_EVENTS, CJK_EVENT]
          else if (scenario === 'grouped-console') eventsToSend = GROUPED_CONSOLE_EVENTS

          if (scenario === 'burst') {
            // Send 20 events rapidly
            const burstEvents: typeof ALL_EVENTS = []
            for (let i = 0; i < 20; i++) {
              const base = ALL_EVENTS[i % ALL_EVENTS.length]
              burstEvents.push({
                ...base,
                redis_id: `${Date.now() + i}-${1000 + i}`,
                event: {
                  ...base.event,
                  event_id: base.event.event_id + `-burst-${i}`,
                  occurred_at: new Date(Date.now() + i * 100).toISOString(),
                },
              })
            }
            eventsToSend = burstEvents
          }

          // Send events with small delays to simulate streaming
          let offset = 0
          const sendNext = () => {
            if (offset >= eventsToSend.length) {
              // Send final cursor and keep alive briefly
              res.write(sseCursorFrame(`${Date.now()}-9999`))
              setTimeout(() => {
                try {
                  res.end()
                } catch {
                  /* client may have disconnected */
                }
              }, 500)
              return
            }
            const entry = eventsToSend[offset]
            res.write(sseFrameForEntry(entry))
            offset += 1
            setTimeout(sendNext, scenario === 'burst' ? 10 : 50)
          }
          setTimeout(sendNext, 100)
          return
        }

        for (const route of FIXTURE_ROUTES) {
          if (route.re.test(pathname)) {
            res.setHeader('Content-Type', 'application/json')
            const handlerBody = route.handler({ url: req.url, method: req.method, body: requestBody }, scenario)
            const body = JSON.stringify(handlerBody)
            if (scenario === 'bed-capacity-conflict' && pathname.endsWith('/assignment')) {
              res.statusCode = 409
              res.end(body)
              return
            }
            if (scenario === 'delayed-control-recovery' && pathname.endsWith('/tail')) {
              const key = counterKey('delayed-control-tail')
              const count = scenarioCounters.get(key) ?? 0
              scenarioCounters.set(key, count + 1)
              if (count === 0) {
                res.statusCode = 503
                res.setHeader('Content-Type', 'application/json')
                res.end(JSON.stringify({ detail: 'control tail unavailable (fixture)' }))
                return
              }
            }
            if (scenario === 'delayed-range' && isDelayedHistoryPath(pathname)) {
              const key = counterKey('delayed-range')
              const count = scenarioCounters.get(key) ?? 0
              scenarioCounters.set(key, count + 1)
              setTimeout(() => res.end(body), 500)
              return
            }
            res.end(body)
            return
          }
        }

        // Let Vite serve static assets from dist.
        if (pathname.startsWith('/assets/')) {
          next()
          return
        }

        // SPA fallback for any other GET.
        if (req.method === 'GET') {
          const indexPath = path.join(DIST_DIR, 'index.html')
          if (fs.existsSync(indexPath)) {
            res.setHeader('Content-Type', 'text/html')
            res.end(fs.readFileSync(indexPath))
            return
          }
        }
        next()
      })

      server.httpServer?.on('upgrade', (req, socket) => {
        log(`WS-UPGRADE ${req.url}`)
        if (!req.url?.startsWith('/ws')) {
          socket.destroy()
          return
        }
        const key = req.headers['sec-websocket-key']
        if (!key) {
          socket.destroy()
          return
        }
        socket.write(
          'HTTP/1.1 101 Switching Protocols\r\n' +
            'Upgrade: websocket\r\n' +
            'Connection: Upgrade\r\n' +
            `Sec-WebSocket-Accept: ${wsAccept(key)}\r\n\r\n`
        )
        socket.write(encodeTextFrame(Buffer.from(JSON.stringify(wsFixtureMessage()))))
        setTimeout(() => socket.end(), 1000)
      })
    },
  }
}

export default defineConfig({
  define: {
    'import.meta.env.VITE_MONITORING_PERF_MARKS': JSON.stringify('1'),
    'import.meta.env.VITE_MONITORING_PERF_INJECT_DELAY_MS': JSON.stringify('12'),
  },
  plugins: [tailwindcss(), react(), monitoringPreviewPlugin()],
  resolve: {
    alias: {
      '@': path.resolve(HERE, './src'),
    },
  },
  preview: {
    host: '127.0.0.1',
    port: FIXTURE_PORT,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})