import type { CalendarConnection, GrowLifecycle } from '../../src/types/calendar'

interface CalendarFixtureRequest {
  url: string
  method?: string
  body?: string
}

interface CalendarFixtureReply {
  status: number
  body: unknown
}

interface CalendarFixtureState {
  connection: CalendarConnection | null
  grow: GrowLifecycle
}

const states = new Map<string, CalendarFixtureState>()
const GROW_ID = '3f2c9a1e-5d47-4b8e-9c6a-7ab90f43d2e1'

export function calendarLifecycleFixtureReply(
  request: CalendarFixtureRequest,
  scenario: string | null,
  session: string,
  origin: string
): CalendarFixtureReply | null {
  const pathname = request.url.split('?')[0]
  if (!pathname.startsWith('/api/calendar/sync/') && !pathname.startsWith('/api/calendar/grow-plans/')) return null
  let state = states.get(session)
  if (!state) {
    state = {
      connection: null,
      grow: {
        grow_plan_id: GROW_ID, crop_batch_id: 1, state: 'closed', calendar_revision: 1,
        original_drying_end: '2026-09-30', first_closed_at: '2026-10-01T04:00:00Z',
        closed_at: '2026-10-01T04:00:00Z', anniversary_at: '2027-10-01T04:00:00Z',
        dataset_id: GROW_ID, data_revision: 1, published_revision: 1,
      },
    }
    states.set(session, state)
  }
  const method = request.method ?? 'GET'
  if (pathname === '/api/calendar/sync/connections' && method === 'GET') return { status: 200, body: state.connection }
  if (pathname === '/api/calendar/sync/connections/test' && method === 'POST') {
    if (scenario === 'calendar-unavailable') return { status: 503, body: { detail: { code: 'calendar_transport_failed' } } }
    return { status: 200, body: [{ name: 'Culture', url: `${origin}/fixture-dav/Culture/`, files_root_url: `${origin}/fixture-dav/Files/`, principal_url: `${origin}/fixture-dav/principal/` }] }
  }
  if (pathname === '/api/calendar/sync/connections' && method === 'POST') {
    const body = JSON.parse(request.body ?? '{}') as Record<string, unknown>
    state.connection = {
      id: 1, instance_id: GROW_ID, generation: 1, files_root_url: `${origin}/fixture-dav/Files/`,
      enabled: false, caldav_base_url: String(body.caldav_base_url ?? ''),
      target_calendar_url: String(body.target_calendar_url ?? ''), account_email: String(body.username ?? ''),
      display_name: 'Guarded Nextcloud fixture', last_sync_at: null, last_error: null,
    }
    return { status: 200, body: state.connection }
  }
  if (/\/sync\/connections\/1\/mirror-preview$/.test(pathname) && method === 'GET') {
    return { status: 200, body: { connection_id: 1, generation: state.connection?.generation ?? 1, event_count: 8, backup_required: true } }
  }
  if (/\/sync\/connections\/1\/reconnect$/.test(pathname) && method === 'POST' && state.connection) {
    const body = JSON.parse(request.body ?? '{}') as Record<string, unknown>
    if (body.backup_confirmed !== true) return { status: 400, body: { detail: { code: 'calendar_backup_required' } } }
    if (body.expected_generation !== state.connection.generation) return { status: 409, body: { detail: { code: 'calendar_generation_conflict' } } }
    state.connection = { ...state.connection, enabled: true, generation: state.connection.generation + 1 }
    return { status: 200, body: { connection_id: 1, generation: state.connection.generation, event_count: 8, sync: { ok: true, confirmed: [1, 2, 3, 4, 5, 6, 7, 8], pending: [], errors: [], pushed: 8, deleted: 0 } } }
  }
  if (pathname === '/api/calendar/sync/run' && method === 'POST') {
    return { status: 200, body: { ok: true, confirmed: [], pending: [], errors: [], pushed: 0, deleted: 0 } }
  }
  if (/\/grow-plans\/[^/]+\/lifecycle$/.test(pathname) && method === 'GET') return { status: 200, body: state.grow }
  if (/\/grow-plans\/[^/]+\/close-preview$/.test(pathname) && method === 'GET') {
    return { status: 200, body: { lifecycle: state.grow, revision: state.grow.calendar_revision, phase_windows: [], drying_end: '2026-09-30', affected_counts: { raw: { measurement: 42 }, cold: { measurement: 42 } }, gaps: [], scope_unresolved: [], conflicts: [], newly_included: {}, released: {}, unavailable: [] } }
  }
  if (/\/grow-plans\/[^/]+\/(?:reopen|close)$/.test(pathname) && method === 'POST') {
    const body = JSON.parse(request.body ?? '{}') as Record<string, unknown>
    if (body.expected_revision !== state.grow.calendar_revision) return { status: 409, body: { detail: { code: 'grow_revision_conflict' } } }
    state.grow = { ...state.grow, state: pathname.endsWith('/reopen') ? 'open' : 'closed', calendar_revision: state.grow.calendar_revision + 1 }
    return { status: 200, body: state.grow }
  }
  return { status: 405, body: { detail: 'Calendar fixture mutation denied' } }
}
