/**
 * Monitoring preview config (test-only).
 *
 * Serves the production `dist` build plus deterministic REST / WebSocket /
 * Grafana-placeholder / SPA-fallback fixtures on
 * `127.0.0.1` at the selected test-only port. It injects a restrictive CSP and writes a request
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
} from './src/features/monitoring/config/fixtures.ts'
import { SOIL_FIXTURE_ROUTES } from './src/features/soil/config/soilFixtures.ts'
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
} from './src/features/event-log/config/fixtures.ts'
import { FIXTURE_PORT, FIXTURE_WS_ORIGIN } from './src/features/monitoring/config/originGuard.ts'
import {
  DASHBOARD_DEVICES,
  DASHBOARD_LIGHT_INTENSITIES,
  dashboardDeviceDetails,
  dashboardControlSnapshotFixture,
} from './tests/monitoring/dashboardDeviceFixtures.ts'
import { z } from 'zod'
import {
  scheduleClockInstant,
  scheduleLocalDates,
  scheduleNextLocalDate,
} from './src/features/climate-timeline/charts/scheduleClock'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DIST_DIR = path.resolve(HERE, 'dist')

const CSP =
  `default-src 'self'; script-src 'self'; connect-src 'self' ${FIXTURE_WS_ORIGIN}; ` +
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

function timelinePeriods(): FixturePeriod[] {
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

const MODE_PROFILE_SCENARIOS: Record<string, true> = {
  'mode-profile-preparation': true,
  'mode-profile-conflict': true,
  'mode-profile-activation-failed': true,
  'mode-profile-partial-light-save': true,
  'mode-profile-stale-response': true,
  'mode-profile-projection-stale': true,
}
const PROFILE_MODES = [
  { id: 1, name: 'veg', photoperiod_hours: 18, is_constant: false },
  { id: 2, name: 'flower', photoperiod_hours: 12, is_constant: false },
  { id: 3, name: 'drying', photoperiod_hours: 0, is_constant: true },
  { id: 4, name: 'sleep', photoperiod_hours: 0, is_constant: true },
]
const PROFILE_SUBMODES = [
  { id: 10, name: 'stretch', week_start: 1, week_end: 3 },
  { id: 11, name: 'bulk', week_start: 4, week_end: 7 },
  { id: 12, name: 'ripen', week_start: 8, week_end: 9 },
]
const fixtureClock = z.string().regex(/^(?:[01]\d|2[0-3]):[0-5]\d(?::00)?$/)
const fixturePeriod = z.object({
  id: z.union([z.number().int(), z.string()]).optional(),
  period_name: z.string().min(1),
  start_time: fixtureClock,
  end_time: fixtureClock,
  ramp_minutes: z.number().int().nonnegative(),
  heating_setpoint: z.number().finite().nullable(),
  cooling_setpoint: z.number().finite().nullable(),
  vpd_setpoint: z.number().finite().nullable(),
  co2_setpoint: z.number().int().nullable(),
  details: z.string(),
})
const fixturePhotoperiod = z.object({
  day_start_time: fixtureClock,
  night_start_time: fixtureClock,
  ramp_up_minutes: z.number().int().nonnegative(),
  ramp_down_minutes: z.number().int().nonnegative(),
})
const fixtureWindow = z.object({
  start: z.string().datetime({ offset: true }),
  end: z.string().datetime({ offset: true }),
  timezone: z.string().min(1),
}).refine(window => Date.parse(window.end) > Date.parse(window.start) &&
  Date.parse(window.end) - Date.parse(window.start) <= 31 * 86_400_000)
const fixtureDraft = z.object({
  request_id: z.string().min(1),
  expected_config_revision: z.string().min(1),
  draft_revision: z.number().int().nonnegative(),
  mode_id: z.number().int().positive(),
  submode_id: z.number().int().positive().nullable().default(null),
  periods: z.array(fixturePeriod).min(1),
  photoperiod: fixturePhotoperiod,
  window: fixtureWindow.optional(),
})
type FixturePeriod = z.infer<typeof fixturePeriod>
type FixtureWindow = z.infer<typeof fixtureWindow>
type FixtureIdentity = { mode_id: number; submode_id: number | null }
type FixtureProfile = FixtureIdentity & {
  location: string
  cluster: string
  parameters_configured: boolean
  periods: FixturePeriod[]
  photoperiod: z.infer<typeof fixturePhotoperiod>
}
type FixtureRoomState = {
  active: FixtureIdentity
  running: FixtureIdentity
  running_registry_version: number
  handover_reads: number
  current_heating_setpoint?: number | null
  forecast_heating_setpoint?: number | null
}
type ModeProfileFixtureState = {
  global_revision: number
  registry_version: number
  rooms: Map<string, FixtureRoomState>
  profiles: Map<string, FixtureProfile>
  light_targets: Record<string, number>
  mutations: Array<Record<string, unknown>>
  counters: Map<string, number>
}
type FixtureReply = { status?: number; body: unknown; delayMs?: number }
const modeProfileSessions = new Map<string, ModeProfileFixtureState>()
const fixtureRevision = (revision: number): string => revision.toString(16).padStart(7, '0')
const fixtureRoomKey = (location: string, cluster: string): string => JSON.stringify([location, cluster])
const fixtureProfileKey = (location: string, cluster: string, identity: FixtureIdentity): string =>
  JSON.stringify([location, cluster, identity.mode_id, identity.submode_id])
const fixtureLightKey = (location: string, cluster: string, device: string, modeId: number): string =>
  JSON.stringify([location, cluster, device, modeId])

function modeProfileState(session: string, scenario: string | null): ModeProfileFixtureState {
  const key = JSON.stringify([session, scenario])
  let state = modeProfileSessions.get(key)
  if (!state) {
    state = {
      global_revision: MODE_PROFILE_SCENARIOS[scenario ?? ''] ? 37 : 9,
      registry_version: MODE_PROFILE_SCENARIOS[scenario ?? ''] ? 9 : 1,
      rooms: new Map(), profiles: new Map(), light_targets: {}, mutations: [], counters: new Map(),
    }
    modeProfileSessions.set(key, state)
  }
  return state
}

function fixtureRoom(
  state: ModeProfileFixtureState, location: string, cluster: string, scenario: string | null
): FixtureRoomState {
  const key = fixtureRoomKey(location, cluster)
  let room = state.rooms.get(key)
  if (!room) {
    const active = location === 'Veg Room'
      ? { mode_id: 1, submode_id: null }
      : scenario === 'sleep-scheduled-flag'
        ? { mode_id: 4, submode_id: null }
        : { mode_id: 2, submode_id: MODE_PROFILE_SCENARIOS[scenario ?? ''] ? 11 : null }
    room = {
      active, running: { ...active }, running_registry_version: state.registry_version,
      handover_reads: 0,
    }
    state.rooms.set(key, room)
    for (const identity of [
      { mode_id: 1, submode_id: null }, { mode_id: 2, submode_id: null },
      { mode_id: 2, submode_id: 10 }, { mode_id: 2, submode_id: 11 },
      { mode_id: 2, submode_id: 12 }, { mode_id: 3, submode_id: null },
      { mode_id: 4, submode_id: null },
    ]) {
      const constant = identity.mode_id >= 3
      const periods: FixturePeriod[] = constant
        ? identity.mode_id === 4 && MODE_PROFILE_SCENARIOS[scenario ?? '']
          ? []
          : [{
              id: 30 + identity.mode_id, period_name: 'Constant', start_time: '00:00:00',
              end_time: '00:00:00', ramp_minutes: 0,
              heating_setpoint: identity.mode_id === 3 ? 18 : MODE_PROFILE_SCENARIOS[scenario ?? ''] ? null : 24,
              cooling_setpoint: identity.mode_id === 3 ? 25 : MODE_PROFILE_SCENARIOS[scenario ?? ''] ? null : 28,
              vpd_setpoint: identity.mode_id === 3 ? 0.9 : MODE_PROFILE_SCENARIOS[scenario ?? ''] ? null : 1.1, co2_setpoint: null,
              details: 'Fixture constant profile',
            }]
        : timelinePeriods().map(period => ({
            ...period,
            heating_setpoint: identity.submode_id === 11 ? 22
              : identity.submode_id === 12 ? 20 : period.heating_setpoint,
          }))
      state.profiles.set(fixtureProfileKey(location, cluster, identity), {
        location, cluster, ...identity,
        parameters_configured: !(identity.mode_id === 4 && MODE_PROFILE_SCENARIOS[scenario ?? '']),
        periods,
        photoperiod: {
          day_start_time: constant ? '00:00:00' : '06:00:00',
          night_start_time: constant ? '00:00:00' : '18:00:00',
          ramp_up_minutes: identity.mode_id === 4 && MODE_PROFILE_SCENARIOS[scenario ?? ''] ? 15 : 20,
          ramp_down_minutes: identity.mode_id === 4 && MODE_PROFILE_SCENARIOS[scenario ?? ''] ? 15 : 20,
        },
      })
    }
    // Completed-tick effective and canonical future values are separate authorities.
    // Inactive profile saves must change neither; a handover samples the newly saved targets.
    room.current_heating_setpoint = state.profiles.get(fixtureProfileKey(location, cluster, active))!.periods[0]?.heating_setpoint ?? null
    room.forecast_heating_setpoint = active.mode_id === 2 ? 24 : room.current_heating_setpoint
    for (const device of DASHBOARD_DEVICES.filter(device => device.location === location && device.cluster === cluster)) {
      for (const mode of PROFILE_MODES)
        state.light_targets[fixtureLightKey(location, cluster, device.device_name, mode.id)] =
          location === 'Flower Room' ? 80 : device.per_room_index === 1 ? 80 : 40
    }
  }
  return room
}

function fixtureIdentityError(location: string, identity: FixtureIdentity): FixtureReply | null {
  if (!PROFILE_MODES.some(mode => mode.id === identity.mode_id) ||
      (identity.submode_id !== null && !PROFILE_SUBMODES.some(mode => mode.id === identity.submode_id)))
    return fixtureError(404, 'profile_not_found', 'Unknown fixture profile')
  if ((location === 'Veg Room' && identity.mode_id !== 1) ||
      (identity.submode_id !== null && identity.mode_id !== 2))
    return fixtureError(422, 'invalid_profile_identity', 'Profile is not allowed in this room')
  return null
}

function fixtureError(status: number, code: string, detail: string): FixtureReply {
  return { status, body: { detail: { code, detail } } }
}

function fixtureCount(state: ModeProfileFixtureState, key: string): number {
  const count = state.counters.get(key) ?? 0
  state.counters.set(key, count + 1)
  return count
}

function fixtureEnvelope(
  profile: FixtureProfile, window: FixtureWindow, revision: string,
  draftRevision: number | null
) {
  const metrics = [
    ['heating_setpoint', 'C'], ['cooling_setpoint', 'C'],
    ['vpd_setpoint', 'kPa'], ['co2_setpoint', 'ppm'],
  ] as const
  if (!profile.periods.some(period => metrics.some(([metric]) => period[metric] !== null)))
    return null
  const startMs = Date.parse(window.start), endMs = Date.parse(window.end)
  const segments: Array<Record<string, unknown>> = []
  const constant = profile.mode_id >= 3
  for (const date of scheduleLocalDates(startMs, endMs)) {
    for (const [index, period] of profile.periods.entries()) {
      const startClock = period.start_time.slice(0, 5), endClock = period.end_time.slice(0, 5)
      const nextDate = scheduleNextLocalDate(date)
      if (!nextDate) continue
      const from = scheduleClockInstant(date, startClock)
      const until = scheduleClockInstant(endClock <= startClock ? nextDate : date, endClock)
      if (from === null || until === null || until <= startMs || from >= endMs) continue
      for (const [metric, unit] of metrics) {
        const value = period[metric]
        const previous = profile.periods[(index + profile.periods.length - 1) % profile.periods.length]![metric]
        const source = {
          mode: String(profile.mode_id), submode: profile.submode_id === null ? null : String(profile.submode_id),
          period: { period_id: String(period.id ?? index + 1), label: period.period_name },
          config_revision: revision, draft_revision: draftRevision === null ? null : String(draftRevision),
        }
        const add = (a: number, b: number, shape: Record<string, unknown>): void => {
          if (b > a) segments.push({
            start: new Date(a).toISOString(), end: new Date(b).toISOString(),
            metric, unit, trajectory_kind: 'scheduled',
            quality: value === null ? 'unavailable' : 'exact', source, ...shape,
          })
        }
        const clipStart = Math.max(from, startMs), clipEnd = Math.min(until, endMs)
        const rampEnd = Math.min(until, from + (constant ? 0 : period.ramp_minutes * 60_000))
        if (value === null) {
          add(clipStart, clipEnd, { shape: 'unavailable', reason: 'Profile target is not configured' })
        } else {
          if (previous !== null && previous !== value && rampEnd > clipStart) {
            const rampUntil = Math.min(rampEnd, clipEnd)
            const at = (time: number): number => previous + (value - previous) * (time - from) / (rampEnd - from)
            add(clipStart, rampUntil, {
              shape: 'linear', start_value: at(clipStart), end_value: at(rampUntil),
            })
            add(rampUntil, clipEnd, { shape: 'step', value })
          } else add(clipStart, clipEnd, { shape: 'step', value })
        }
      }
    }
  }
  if (!segments.length) return null
  return {
    contract_version: 1, room: profile.location, generated_at: new Date().toISOString(), window,
    revision_scope: draftRevision === null ? 'saved' : 'draft',
    base_config_revision: revision, draft_revision: draftRevision === null ? null : String(draftRevision),
    segments, assumptions: ['Hypothetical exact-profile Toronto schedule, not running control facts.'],
    warnings: [],
  }
}

function fixtureModeResponse(profile: FixtureProfile, scenario: string | null): Record<string, unknown> {
  const mode = PROFILE_MODES.find(mode => mode.id === profile.mode_id)!
  return {
    location: profile.location, cluster: profile.cluster, mode_id: profile.mode_id,
    submode_id: profile.submode_id, mode_name: mode.name,
    submode_name: PROFILE_SUBMODES.find(submode => submode.id === profile.submode_id)?.name ?? null,
    is_constant: scenario === 'veg-constant-flag' ? true
      : scenario === 'sleep-scheduled-flag' ? false : mode.is_constant,
    parameters: {
      day_start_time: profile.photoperiod.day_start_time,
      night_start_time: profile.photoperiod.night_start_time,
      light_ramp_up_minutes: profile.photoperiod.ramp_up_minutes,
      light_ramp_down_minutes: profile.photoperiod.ramp_down_minutes,
      main_light_intensity: 100, supplemental_light_intensity: 0,
    },
  }
}

function profileFixtureReply(
  req: { url: string; method?: string; body?: string; referer?: string },
  scenario: string | null, session: string
): FixtureReply | null {
  const url = new URL(req.url, 'http://fixture.invalid')
  const pathname = url.pathname, method = req.method ?? 'GET'
  const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent)
  const newScenario = Boolean(MODE_PROFILE_SCENARIOS[scenario ?? ''])
  const relevant = pathname === '/__fixture/mode-profile-state' ||
    /^\/api\/room-modes\//.test(pathname) || /^\/api\/climate-timeline\//.test(pathname) ||
    /^\/api\/lights\//.test(pathname) ||
    /^\/api\/climate-periods\//.test(pathname) ||
    /^\/api\/monitoring\/control\/[^/]+\/current$/.test(pathname) ||
    (/^\/api\/monitoring\/control\/[^/]+\/projection$/.test(pathname) &&
      (newScenario || (req.referer ?? '').includes('/control'))) ||
    (scenario !== 'relay-pid-timeline' &&
      (pathname === '/api/devices/control-snapshot' || pathname === '/api/devices/registry' ||
        /^\/api\/devices\/[^/]+\/[^/]+$/.test(pathname)))
  if (!relevant) return null
  const state = modeProfileState(session, scenario)
  const location = parts[1] === 'room-modes' ? parts[3] ?? 'Flower Room'
    : parts[1] === 'monitoring' ? parts[3] ?? 'Flower Room' : parts[2] ?? 'Flower Room'
  const cluster = parts[1] === 'room-modes' ? parts[4] ?? 'main'
    : parts[1] === 'monitoring' ? 'main' : parts[3] ?? 'main'
  if (pathname === '/__fixture/mode-profile-state') {
    if (method !== 'GET') return fixtureError(405, 'read_only_fixture', 'State endpoint is read-only')
    const room = fixtureRoom(state, url.searchParams.get('location') ?? 'Flower Room', url.searchParams.get('cluster') ?? 'main', scenario)
    return { body: {
      active: room.active, running: room.running, global_revision: state.global_revision,
      registry_version: state.registry_version, profiles: [...state.profiles.values()],
      light_targets: state.light_targets, mutations: state.mutations,
    } }
  }
  if (pathname === '/api/room-modes/modes')
    return { body: PROFILE_MODES.map(mode => ({
      ...mode, is_constant: scenario === 'veg-constant-flag' && mode.id === 1 ? true
        : scenario === 'sleep-scheduled-flag' && mode.id === 4 ? false : mode.is_constant,
    })) }
  if (pathname === '/api/room-modes/submodes') return { body: PROFILE_SUBMODES }
  if (pathname === '/api/devices/registry') return { body: DASHBOARD_DEVICES }
  if (pathname === '/api/devices/control-snapshot') {
    fixtureRoom(state, 'Flower Room', 'main', scenario)
    return { body: { ...dashboardControlSnapshotFixture(), registry_version: state.registry_version } }
  }
  if (/^\/api\/devices\/[^/]+\/[^/]+$/.test(pathname)) {
    if (method !== 'GET') return fixtureError(405, 'read_only_inventory', 'Device inventory is read-only')
    return { body: { location, cluster, devices: dashboardDeviceDetails(location, cluster) } }
  }
  if (/^\/api\/lights\/\d+\/intensity$/.test(pathname)) {
    const device = DASHBOARD_DEVICES.find(device => device.device_id === Number(parts[2]))
    if (!device) return fixtureError(404, 'device_not_found', 'Unknown fixture light')
    return fixtureLightReply(state, device.location, device.cluster, device.device_name, method, req.body, pathname, scenario)
  }
  const room = fixtureRoom(state, location, cluster, scenario)
  const activeProfile = state.profiles.get(fixtureProfileKey(location, cluster, room.active))!
  if (parts[1] === 'climate-periods') {
    const identity = url.searchParams.has('mode_id') ? {
      mode_id: Number(url.searchParams.get('mode_id')),
      submode_id: url.searchParams.has('submode_id') ? Number(url.searchParams.get('submode_id')) : null,
    } : room.active
    const error = fixtureIdentityError(location, identity)
    if (error) return error
    return { body: state.profiles.get(fixtureProfileKey(location, cluster, identity))!.periods }
  }
  if (parts[1] === 'lights') {
    if (parts[4] === 'zone-status') return { body: {
      lights: DASHBOARD_DEVICES.filter(device => device.location === location && device.cluster === cluster)
        .map(device => fixtureLightStatus(state, location, cluster, device.device_name, room.active.mode_id)),
    } }
    return fixtureLightReply(state, location, cluster, parts[4] ?? '', method, req.body, pathname, scenario)
  }
  if (parts[1] === 'room-modes') {
    if (pathname.endsWith('/mode') && method === 'POST') {
      const parsed = z.object({
        mode_name: z.string(), submode_name: z.string().nullable().optional(),
        expected_config_revision: z.string().optional(),
      }).safeParse(parseFixtureBody(req.body))
      if (!parsed.success) return fixtureError(422, 'invalid_request', parsed.error.message)
      const body = parsed.data
      const mode = PROFILE_MODES.find(mode => mode.name === body.mode_name)
      const submode = body.submode_name == null ? null : PROFILE_SUBMODES.find(submode => submode.name === body.submode_name)
      const identity = { mode_id: mode?.id ?? -1, submode_id: submode === null ? null : submode?.id ?? -1 }
      const error = fixtureIdentityError(location, identity)
      if (error) return error
      const mutation = { method, path: pathname, ...identity, expected_config_revision: body.expected_config_revision }
      if (body.expected_config_revision != null && body.expected_config_revision !== fixtureRevision(state.global_revision)) {
        state.mutations.push({ ...mutation, success: false, code: 'activation_revision_conflict' })
        return fixtureError(409, 'activation_revision_conflict', 'Configuration changed before activation')
      }
      if (scenario === 'mode-profile-activation-failed') {
        state.mutations.push({ ...mutation, success: false, code: 'activation_failed' })
        return fixtureError(503, 'activation_failed', 'Fixture activation failed; saved preparation remains')
      }
      const profile = state.profiles.get(fixtureProfileKey(location, cluster, identity))!
      if (!profile.parameters_configured) return fixtureError(409, 'profile_not_configured', 'Save profile before activation')
      room.active = identity
      state.global_revision += 1
      state.registry_version += 1
      room.handover_reads = 1
      state.mutations.push({ ...mutation, success: true, config_revision: fixtureRevision(state.global_revision), registry_version: state.registry_version })
      return { body: {
        ...fixtureModeResponse(profile, scenario), config_revision: fixtureRevision(state.global_revision),
        runtime_ready: true, warning: null,
      } }
    }
    return { body: fixtureModeResponse(activeProfile, scenario) }
  }
  if (parts[1] === 'monitoring') {
    const observed = Date.now(), observedAt = new Date(observed).toISOString()
    const currentVersion = {
      contract_version: 1, config_version: state.global_revision,
      revision: fixtureRevision(room.running_registry_version),
    }
    if (pathname.endsWith('/current')) {
      // One old completed tick after activation disagrees with the new registry.
      // Only the next current read confirms the installed identity.
      if (room.handover_reads === 0) {
        if (room.running.mode_id !== room.active.mode_id || room.running.submode_id !== room.active.submode_id) {
          room.current_heating_setpoint = activeProfile.periods[0]?.heating_setpoint ?? null
          room.forecast_heating_setpoint = room.current_heating_setpoint
        }
        room.running = { ...room.active }
        room.running_registry_version = state.registry_version
        currentVersion.revision = fixtureRevision(state.registry_version)
      } else room.handover_reads -= 1
      const validUntil = new Date(observed + 4_000).toISOString()
      const profile = state.profiles.get(fixtureProfileKey(location, cluster, room.running))!
      const heat = room.current_heating_setpoint ?? null
      const normalize = (value: string): string => {
        const normalized = value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
        return /^\d/.test(normalized) ? `v_${normalized}` : normalized
      }
      const prefix = `${normalize(location)}.${normalize(cluster)}.setpoint.`
      const facts: Array<[string, number | null]> = [
        ['effective_heating_setpoint', heat],
        ['effective_cooling_setpoint', profile.periods[0]?.cooling_setpoint ?? null],
        ['effective_vpd_setpoint', profile.periods[0]?.vpd_setpoint ?? null],
        ['effective_co2_setpoint', profile.periods[0]?.co2_setpoint ?? null],
        ['profile_mode_id', room.running.mode_id],
      ]
      if (room.running.submode_id !== null) facts.push(['profile_submode_id', room.running.submode_id])
      return { body: { quality: 'exact', value: {
        version: currentVersion, observed_at: observedAt, valid_until: validUntil,
        series: facts.map(([metric, value]) => ({
          series_id: { value: prefix + metric }, observed_at: observedAt, valid_until: validUntil,
          value, quality: value === null ? 'unavailable' : 'exact',
        })),
        photoperiod: { observed_at: observedAt, valid_until: validUntil,
          phase: room.running.mode_id >= 3 ? 'MOON' : 'SUN', quality: 'exact' },
        persistence: { state: 'pending' },
      } } }
    }
    if (pathname.endsWith('/projection')) {
      if (scenario === 'missing-projection' || process.env.MONITORING_SCENARIO === 'flower-partial')
        return { body: { quality: 'unavailable', value: [] } }
      const from = observedAt, until = new Date(observed + 86_400_000).toISOString()
      const profile = state.profiles.get(fixtureProfileKey(location, cluster, room.running))!
      const heating = room.forecast_heating_setpoint ?? null
      const values: Array<[string, number | null]> = [
        ['heating_setpoint', heating], ['cooling_setpoint', profile.periods[0]?.cooling_setpoint ?? null],
        ['vpd_setpoint', profile.periods[0]?.vpd_setpoint ?? null],
        ['co2_setpoint', profile.periods[0]?.co2_setpoint ?? null],
      ]
      const operational = fixtureEnvelope(profile, { start: from, end: until, timezone: 'UTC' },
        fixtureRevision(state.global_revision), null)
      const trajectory = operational !== null && scenario === 'calendar-transition-skipped' ? {
        ...operational,
        warnings: [{
          code: 'calendar.transition_skipped', detail: 'Calendar destination flower/bulk was skipped',
          reason: 'unknown_mode', start: from, end: new Date(observed + 3_600_000).toISOString(),
        }],
      } : operational
      return { body: { quality: 'estimated', value: [{
        version: scenario === 'mode-profile-projection-stale'
          ? { ...currentVersion, config_version: currentVersion.config_version + 1 } : currentVersion,
        generated_at: observedAt, valid_from: from, valid_until: until,
        series: values.map(([metric, value]) => ({
          series_id: { value: `climate.${metric}_target` }, value,
          quality: value === null ? 'unavailable' : 'estimated', valid_from: from, valid_until: until,
        })),
      }], trajectory } }
    }
  }
  if (parts[1] !== 'climate-timeline') return null
  const action = parts[4]
  let identity: FixtureIdentity
  let draft: z.infer<typeof fixtureDraft> | undefined
  if (method === 'POST') {
    const parsed = fixtureDraft.safeParse(parseFixtureBody(req.body))
    if (!parsed.success) return fixtureError(422, 'invalid_request', parsed.error.message)
    draft = parsed.data
    identity = { mode_id: draft.mode_id, submode_id: draft.submode_id }
  } else identity = action === 'profile' || action === 'configuration'
    ? { mode_id: Number(url.searchParams.get('mode_id')), submode_id: url.searchParams.has('submode_id') ? Number(url.searchParams.get('submode_id')) : null }
    : room.active
  const identityError = fixtureIdentityError(location, identity)
  if (identityError) return identityError
  const profile = state.profiles.get(fixtureProfileKey(location, cluster, identity))!
  const revision = fixtureRevision(state.global_revision)
  if (draft) {
    const mutation = { method, path: pathname, location, cluster, ...identity, request_id: draft.request_id, expected_config_revision: draft.expected_config_revision, draft_revision: draft.draft_revision }
    if (draft.expected_config_revision !== revision || scenario === 'mode-profile-conflict') {
      state.mutations.push({ ...mutation, success: false, code: 'timeline_revision_conflict' })
      return fixtureError(409, 'timeline_revision_conflict', 'Saved configuration changed; draft was not committed')
    }
    if (newScenario && !fixtureCoverageValid(draft.periods, identity.mode_id >= 3))
      return fixtureError(422, 'invalid_period_coverage', 'Periods must cover the day exactly without overlap')
    if (action === 'apply') {
      profile.periods = structuredClone(draft.periods)
      profile.photoperiod = { ...draft.photoperiod }
      profile.parameters_configured = true
      state.global_revision += 1
      if (identity.mode_id === room.active.mode_id && identity.submode_id === room.active.submode_id) {
        state.registry_version += 1
        room.running_registry_version = state.registry_version
        room.current_heating_setpoint = profile.periods[0]?.heating_setpoint ?? null
        room.forecast_heating_setpoint = room.current_heating_setpoint
      }
      state.mutations.push({ ...mutation, success: true, config_revision: fixtureRevision(state.global_revision),
        periods: structuredClone(profile.periods), photoperiod: { ...profile.photoperiod }, parameters_configured: true })
      return { body: {
        request_id: draft.request_id, config_revision: fixtureRevision(state.global_revision),
        ...identity, periods: profile.periods, photoperiod: profile.photoperiod,
        parameters_configured: true, notification_warning: null,
      } }
    }
    if (action !== 'preview' || draft.window === undefined)
      return fixtureError(422, 'invalid_request', 'Preview requires a window')
    state.mutations.push({ ...mutation, success: true, window: { ...draft.window },
      periods: structuredClone(draft.periods), photoperiod: { ...draft.photoperiod } })
    const trajectory = fixtureEnvelope({ ...profile, periods: draft.periods, photoperiod: draft.photoperiod },
      draft.window, scenario === 'timeline-preview-stale' ? 'stale-config-revision' : revision,
      scenario === 'timeline-preview-stale' ? draft.draft_revision + 1 : draft.draft_revision)
    if (scenario === 'timeline-wrong-room' && trajectory !== null)
      trajectory.room = location === 'Flower Room' ? 'Veg Room' : 'Flower Room'
    const delayMs = scenario === 'mode-profile-stale-response' &&
      fixtureCount(state, 'stale:first-preview') === 0 ? 1_200 : 0
    return { delayMs, body: {
      request_id: draft.request_id, expected_config_revision: draft.expected_config_revision,
      draft_revision: draft.draft_revision, ...identity, window: draft.window, trajectory,
    } }
  }
  const parsedWindow = fixtureWindow.safeParse({
    start: url.searchParams.get('start'), end: url.searchParams.get('end'),
    timezone: url.searchParams.get('timezone') ?? 'America/Toronto',
  })
  if (!parsedWindow.success) return fixtureError(422, 'invalid_window', 'Profile GET requires an aware window')
  const window = parsedWindow.data
  let delayMs = 0
  if (scenario === 'mode-profile-stale-response') {
    if (identity.mode_id === 3 && fixtureCount(state, 'stale:first-drying-profile') === 0) delayMs = 1_000
    const windowKey = `${location}:${cluster}:initial-window`
    const initialWindow = state.counters.get(windowKey)
    if (initialWindow === undefined) state.counters.set(windowKey, Date.parse(window.start))
    else if (initialWindow !== Date.parse(window.start) &&
      fixtureCount(state, 'stale:first-changed-window') === 0) delayMs = 1_000
  }
  return { delayMs, body: {
    config_revision: revision, ...identity, parameters_configured: profile.parameters_configured,
    periods: structuredClone(profile.periods), photoperiod: { ...profile.photoperiod }, window,
    trajectory: action === 'configuration' ? null : fixtureEnvelope(profile, window, revision, null),
  } }
}

function parseFixtureBody(body: string | undefined): unknown {
  try { return JSON.parse(body ?? '{}') } catch { return null }
}

function fixtureCoverageValid(periods: FixturePeriod[], constant: boolean): boolean {
  const minute = (clock: string): number => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5))
  if (constant) return periods.length === 1 && minute(periods[0]!.start_time) === 0 && minute(periods[0]!.end_time) === 0
  const intervals: Array<[number, number]> = []
  for (const period of periods) {
    const start = minute(period.start_time), end = minute(period.end_time)
    if (start === end) return periods.length === 1
    if (end > start) intervals.push([start, end])
    else { intervals.push([start, 1440]); if (end > 0) intervals.push([0, end]) }
  }
  intervals.sort((a, b) => a[0] - b[0])
  let cursor = 0
  for (const [start, end] of intervals) {
    if (start !== cursor) return false
    cursor = end
  }
  return cursor === 1440
}

function fixtureLightStatus(
  state: ModeProfileFixtureState, location: string, cluster: string, name: string, modeId: number
): Record<string, unknown> {
  const device = DASHBOARD_DEVICES.find(device => device.location === location && device.device_name === name)
  const target = state.light_targets[fixtureLightKey(location, cluster, name, modeId)] ?? null
  return {
    location, cluster, device: name, display_name: device?.display_name ?? name,
    intensity: DASHBOARD_LIGHT_INTENSITIES[`${location}_${cluster}_${name}_intensity`] ?? 0,
    target_intensity: target, day_target_intensity: target, schedule_sun_target_intensity: target,
    scheduler_nominal_intensity: target, voltage: 0, board_id: device?.board_id ?? 0,
    channel: device?.dimming_channel ?? 0,
  }
}

function fixtureLightReply(
  state: ModeProfileFixtureState, location: string, cluster: string, name: string,
  method: string, rawBody: string | undefined, pathname: string, scenario: string | null
): FixtureReply {
  const room = fixtureRoom(state, location, cluster, scenario)
  const device = DASHBOARD_DEVICES.find(device => device.location === location && device.cluster === cluster && device.device_name === name)
  if (!device) return fixtureError(404, 'device_not_found', 'Unknown fixture light')
  if (method === 'GET') {
    if (pathname.endsWith('/schedule')) {
      const profile = state.profiles.get(fixtureProfileKey(location, cluster, room.active))!
      return { body: {
        start_time: profile.photoperiod.day_start_time, end_time: profile.photoperiod.night_start_time,
        target_intensity: state.light_targets[fixtureLightKey(location, cluster, name, room.active.mode_id)] ?? null,
      } }
    }
    return { body: fixtureLightStatus(state, location, cluster, name, room.active.mode_id) }
  }
  const parsed = z.object({
    target_intensity: z.number().finite().min(0).max(100),
    expected_mode_id: z.number().int().positive().optional(),
  }).safeParse(parseFixtureBody(rawBody))
  if (!parsed.success) return fixtureError(422, 'invalid_request', parsed.error.message)
  const body = parsed.data
  const mutation = {
    method, path: pathname, location, cluster, device: name, device_id: device.device_id,
    mode_id: room.active.mode_id, expected_mode_id: body.expected_mode_id,
    target_intensity: body.target_intensity,
  }
  if (body.expected_mode_id != null && body.expected_mode_id !== room.active.mode_id) {
    state.mutations.push({ ...mutation, success: false, code: 'light_target_mode_changed' })
    return fixtureError(409, 'light_target_mode_changed', 'Active fixture mode changed; no light target was saved')
  }
  if (scenario === 'mode-profile-partial-light-save' && name === 'light_f_2') {
    state.mutations.push({ ...mutation, success: false, code: 'fixture_light_save_failed' })
    return fixtureError(503, 'fixture_light_save_failed', 'Apache fixture target could not be saved')
  }
  const key = fixtureLightKey(location, cluster, name, room.active.mode_id)
  const previousTarget = state.light_targets[key]
  state.light_targets[key] = body.target_intensity
  state.global_revision += 1
  state.registry_version += 1
  room.running_registry_version = state.registry_version
  state.mutations.push({ ...mutation, success: true, previous_target_intensity: previousTarget,
    config_revision: fixtureRevision(state.global_revision), registry_version: state.registry_version })
  return { body: {
    success: true, location, cluster, device: name, device_id: device.device_id,
    target_intensity: body.target_intensity, rows_updated: 1,
  } }
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
    re: /^\/api\/calendar\/events$/,
    handler: req => {
      const method = req.method ?? 'GET'
      const today = new Date()
      const plus = (n: number): string => {
        const d = new Date(today)
        d.setDate(d.getDate() + n)
        return d.toISOString().slice(0, 10)
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
              /^\/api\/room-modes\//.test(pathname) ||
              /^\/api\/lights\//.test(pathname) ||
              /^\/api\/pid\/(?:parameters|mode)\//.test(pathname) ||
              /^\/api\/calendar\/events/.test(pathname))) ||
          (req.method === 'PUT' && /^\/api\/lights\//.test(pathname)) ||
          (req.method === 'PATCH' && /^\/api\/calendar\/events/.test(pathname))
        const isSensorBulkRead = req.method === 'POST' && pathname === '/api/sensor-data'
        const requestBody = isFixtureMutation || isSensorBulkRead ? await readRequestBody(req) : undefined
        const scenario = scenarioFrom(req.url ?? '') ?? scenarioFrom(req.headers.referer ?? '')
        const fixtureSession =
          new URLSearchParams((req.url ?? '').split('?')[1] ?? '').get('fixtureSession') ??
          new URLSearchParams((req.headers.referer ?? '').split('?')[1] ?? '').get('fixtureSession') ??
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
          /\/(?:profile|configuration)$/.test(pathname)
        ) {
          res.statusCode = 503
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ detail: 'saved timeline unavailable (fixture)' }))
          return
        }
        if (
          scenario === 'timeline-unavailable-409' &&
          req.method === 'GET' &&
          /\/(?:profile|configuration)$/.test(pathname)
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

        const profileReply = profileFixtureReply({
          url: req.url ?? '/', method: req.method, body: requestBody, referer: req.headers.referer,
        }, scenario, fixtureSession)
        if (profileReply !== null) {
          // Capture authority before waiting so older responses cannot observe later writes.
          const body = JSON.stringify(profileReply.body)
          if (profileReply.delayMs)
            await new Promise<void>(resolve => setTimeout(resolve, profileReply.delayMs))
          if (!res.destroyed && !res.writableEnded) {
            res.statusCode = profileReply.status ?? 200
            res.setHeader('Content-Type', 'application/json')
            res.end(body)
          }
          return
        }

        for (const route of FIXTURE_ROUTES) {
          if (route.re.test(pathname)) {
            res.setHeader('Content-Type', 'application/json')
            const handlerBody = route.handler(
              { url: req.url, method: req.method, body: requestBody },
              scenario
            )
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
        socket.on('error', error => {
          log(`WS-SOCKET-ERROR ${error.message}`)
          socket.destroy()
        })
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
        const closeTimer = setTimeout(() => {
          if (!socket.destroyed) socket.end()
        }, 1000)
        socket.once('close', () => clearTimeout(closeTimer))
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
