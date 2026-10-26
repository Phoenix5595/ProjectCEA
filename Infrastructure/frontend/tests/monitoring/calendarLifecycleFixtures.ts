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

/**
 * One grow-plan phase of the `calendar-mode-change` scenario. The shape copies
 * a `buildCalendarVisualFixture` manual item (camelCase DTO fields, normalized
 * by `normalizeCalendarEvent`) plus the grow membership and phase order that
 * +1 week and its forward shift need.
 */
interface ModeChangeEvent {
  id: string
  source: 'crop_batch'
  eventType: string
  title: string
  start: string
  end: string
  location: string
  cluster: string
  editable: boolean
  colorKey: string
  growPlanId: string
  numericId: number
  metadata: { phase_order: number }
  sync_status: 'synced'
}

interface CalendarFixtureState {
  connection: CalendarConnection | null
  grow: GrowLifecycle
  modeChangeEvents?: ModeChangeEvent[]
}

const states = new Map<string, CalendarFixtureState>()
const GROW_ID = '3f2c9a1e-5d47-4b8e-9c6a-7ab90f43d2e1'
const BULK_EVENT_ID = 501
const RIPEN_EVENT_ID = 502
const BULK_PHASE_ORDER = 6
const RIPEN_PHASE_ORDER = 7

function shiftDay(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

/** Bulk then ripening: one grow, consecutive phase orders, so +1 week shifts ripening too. */
function modeChangeEvents(): ModeChangeEvent[] {
  const dateParts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/Toronto', year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(new Date()).map(part => [part.type, part.value])
  )
  const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`
  const phase = (
    eventType: string, title: string, numericId: number, phaseOrder: number,
    startDay: number, endDay: number
  ): ModeChangeEvent => ({
    id: `${GROW_ID}-${eventType}`, source: 'crop_batch', eventType, title,
    start: shiftDay(today, startDay), end: shiftDay(today, endDay),
    location: 'Flower Room', cluster: 'main', editable: false, colorKey: eventType,
    growPlanId: GROW_ID, numericId, metadata: { phase_order: phaseOrder }, sync_status: 'synced',
  })
  return [
    phase('flower_bulk', 'Bulk phase', BULK_EVENT_ID, BULK_PHASE_ORDER, -3, 10),
    phase('flower_ripen', 'Ripen phase', RIPEN_EVENT_ID, RIPEN_PHASE_ORDER, 11, 31),
  ]
}

export function calendarLifecycleFixtureReply(
  request: CalendarFixtureRequest,
  scenario: string | null,
  session: string,
  origin: string
): CalendarFixtureReply | null {
  const pathname = request.url.split('?')[0]
  const modeChangeScenario = scenario === 'calendar-mode-change'
  const modeChangePath = modeChangeScenario &&
    (pathname === '/api/calendar/events' || pathname.startsWith('/api/calendar/expected-harvest/'))
  if (!pathname.startsWith('/api/calendar/sync/') && !pathname.startsWith('/api/calendar/grow-plans/') &&
      !modeChangePath) return null
  let state = states.get(session)
  if (!state) {
    state = {
      connection: null,
      grow: {
        grow_plan_id: GROW_ID, crop_batch_id: 1, state: modeChangeScenario ? 'open' : 'closed', calendar_revision: 1,
        original_drying_end: '2026-09-30', first_closed_at: '2026-10-01T04:00:00Z',
        closed_at: '2026-10-01T04:00:00Z', anniversary_at: '2027-10-01T04:00:00Z',
        dataset_id: GROW_ID, data_revision: 1, published_revision: 1,
      },
      modeChangeEvents: modeChangeScenario ? modeChangeEvents() : undefined,
    }
    states.set(session, state)
  }
  const method = request.method ?? 'GET'
  if (pathname === '/api/calendar/events' && method === 'GET')
    return { status: 200, body: { items: state.modeChangeEvents ?? [], next_cursor: null } }
  if (pathname.startsWith('/api/calendar/expected-harvest/') && method === 'GET') {
    const parts = pathname.split('/').filter(Boolean).map(decodeURIComponent)
    const ripen = state.modeChangeEvents?.find(event => event.metadata.phase_order === RIPEN_PHASE_ORDER)
    if (!ripen || parts[3] !== 'Flower Room' || parts[4] !== 'main') return { status: 200, body: null }
    return { status: 200, body: { harvest_date: ripen.end, grow_plan_id: GROW_ID, event_id: RIPEN_EVENT_ID } }
  }
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
  if (/\/grow-plans\/[^/]+\/extend$/.test(pathname) && method === 'POST') {
    const events = state.modeChangeEvents
    if (!events) return { status: 405, body: { detail: 'Calendar fixture mutation denied' } }
    const body = JSON.parse(request.body ?? '{}') as { event_id?: number; days?: number }
    const target = events.find(event => event.numericId === body.event_id)
    if (!target) return { status: 404, body: { detail: { code: 'grow_phase_not_found' } } }
    // The extended phase keeps its start; only this plan's later phases move.
    const days = Number(body.days ?? 0)
    state.modeChangeEvents = events.map(event => {
      if (event.numericId === target.numericId) return { ...event, end: shiftDay(event.end, days) }
      if (event.metadata.phase_order <= target.metadata.phase_order) return event
      return { ...event, start: shiftDay(event.start, days), end: shiftDay(event.end, days) }
    })
    return { status: 200, body: { grow_plan_id: GROW_ID, events: state.modeChangeEvents } }
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
