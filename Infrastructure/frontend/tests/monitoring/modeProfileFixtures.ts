/**
 * Mode-profile, climate-timeline and light-target fixtures for the monitoring
 * preview (`vite.monitoring.config.ts`). Per-session state lives in
 * `modeProfileSessions`; `profileFixtureReply` produces every reply.
 */
import { z } from 'zod'

import {
  scheduleClockInstant,
  scheduleLocalDates,
  scheduleNextLocalDate,
} from '../../src/features/climate-timeline/charts/scheduleClock.ts'

import {
  DASHBOARD_DEVICES,
  DASHBOARD_LIGHT_INTENSITIES,
  dashboardDeviceDetails,
  dashboardControlSnapshotFixture,
} from './dashboardDeviceFixtures.ts'

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
  /** Session-owned relay bindings: device id → MCP channel (null = unbound). */
  relay_channels: Map<number, number | null>
  mutations: Array<Record<string, unknown>>
  counters: Map<string, number>
}
export type FixtureReply = { status?: number; body: unknown; delayMs?: number }
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
      relay_channels: new Map(DASHBOARD_DEVICES.map(device => [device.device_id, device.channel])),
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
    const active = location === 'Veg Room' || scenario === 'calendar-mode-change'
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

export function profileFixtureReply(
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
        /^\/api\/devices\/registry\/\d+\/relay$/.test(pathname) ||
        /^\/api\/devices\/[^/]+\/[^/]+$/.test(pathname))) ||
    /^\/api\/calendar\/pending-mode-change\//.test(pathname)
  if (!relevant) return null
  const state = modeProfileState(session, scenario)
  if (pathname.startsWith('/api/calendar/pending-mode-change/')) {
    if (method !== 'GET') return fixtureError(405, 'read_only_fixture', 'Pending change is read-only')
    const pendingLocation = parts[3] ?? '', pendingCluster = parts[4] ?? ''
    const activeMode = fixtureRoom(state, pendingLocation, pendingCluster, scenario).active.mode_id
    const pending = scenario === 'calendar-mode-change' && pendingLocation === 'Flower Room' &&
      pendingCluster === 'main' && activeMode === 1
    return { body: pending ? {
      from_mode: 'veg', to_mode: 'flower', to_submode: 'stretch', event_id: 500,
      since: new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/Toronto', year: 'numeric', month: '2-digit', day: '2-digit',
      }).format(new Date()),
    } : null }
  }
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
  if (pathname === '/api/devices/registry')
    return { body: DASHBOARD_DEVICES.map(device => ({
      ...device, channel: state.relay_channels.get(device.device_id) ?? null,
    })) }
  if (pathname === '/api/devices/control-snapshot') {
    fixtureRoom(state, 'Flower Room', 'main', scenario)
    return { body: {
      ...dashboardControlSnapshotFixture(device => state.relay_channels.get(device.device_id) ?? null),
      registry_version: state.registry_version,
    } }
  }
  if (/^\/api\/devices\/registry\/\d+\/relay$/.test(pathname)) {
    if (method !== 'PUT') return fixtureError(405, 'read_only_inventory', 'Relay moves require PUT')
    const deviceId = Number(parts[3])
    if (!DASHBOARD_DEVICES.some(candidate => candidate.device_id === deviceId))
      return fixtureError(404, 'device_not_found', 'Unknown fixture device')
    const parsed = z.object({ channel: z.number().int().min(0).max(15) })
      .safeParse(parseFixtureBody(req.body))
    if (!parsed.success) return fixtureError(422, 'invalid_request', parsed.error.message)
    if (scenario === 'relay-move-unsafe')
      return { status: 503, body: { detail: 'Unable to make relay channel 13 safe' } }
    const target = parsed.data.channel
    const source = state.relay_channels.get(deviceId) ?? null
    // A drop onto an occupied channel swaps the two owners; both were OFF.
    const owner = source === target ? null : [...state.relay_channels.entries()]
      .find(([id, channel]) => id !== deviceId && channel === target)?.[0] ?? null
    if (source !== target) {
      state.relay_channels.set(deviceId, target)
      if (owner !== null) state.relay_channels.set(owner, source)
      state.registry_version += 1
    }
    state.mutations.push({ method, path: pathname, device_id: deviceId, channel: target, success: true })
    return { body: {
      device_id: deviceId, channel: target, swapped_device_id: owner,
      swapped_channel: owner === null ? null : source,
    } }
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
