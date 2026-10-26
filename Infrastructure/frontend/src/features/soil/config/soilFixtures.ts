/**
 * Deterministic soil fixtures for the guarded monitoring preview: sensor
 * registry (CAN + RS-485), placement mutations, RS-485 bus status, beds,
 * heatmap, live soil and soil history. Placement state is kept per
 * `fixtureSession` so specs that move probes never leak into each other.
 * Every response stays on the fixture origin.
 */
export interface SoilFixtureRequest {
  url: string
  method?: string
  body?: string
}

export interface SoilFixtureReply {
  status: number
  body: unknown
}

const NOW = '2026-09-21T12:00:00Z'
const HOUR_MS = 3_600_000

const SOIL_UNITS: Record<string, string> = {
  temperature: '°C',
  water_content: '%',
  ec: 'µS/cm',
  ph: 'pH',
}

const BEDS = [
  { rack_id: 1, name: 'Back Bed', width_ft: 4, length_ft: 4 },
  { rack_id: 5, name: 'Front Bed', width_ft: 4, length_ft: 4 },
]

/** Per-address metric values (addresses 4, 5 placed; 6 the closed placement). */
const METRIC_VALUES: Record<string, Record<number, number>> = {
  water_content: { 4: 32.1, 5: 28.4, 6: 41.0 },
  temperature: { 4: 21.4, 5: 22.0, 6: 20.5 },
  ec: { 4: 1180, 5: 1020, 6: 1400 },
  ph: { 4: 6.6, 5: 6.4, 6: 6.9 },
}

type Assignment =
  | { kind: 'can'; room: string; location_in_room: string }
  | {
      kind: 'rs485'
      room: string
      bed: string | null
      x_frac: number | null
      y_frac: number | null
    }

interface FixtureRecord {
  registry_id: number
  bus: 'can' | 'rs485'
  hardware_address: number
  sensor_uid: string
  identity_status: 'confirmed'
  pending_candidate_registry_id: null
  pending_since: null
  display_name: string
  status: 'assigned' | 'unassigned'
  first_seen: string
  last_seen: string
  assignment: Assignment | null
}

function record(
  registryId: number,
  bus: 'can' | 'rs485',
  hardwareAddress: number,
  displayName: string,
  assignment: Assignment | null
): FixtureRecord {
  return {
    registry_id: registryId,
    bus,
    hardware_address: hardwareAddress,
    sensor_uid: `00000000-0000-4000-8000-${String(registryId).padStart(12, '0')}`,
    identity_status: 'confirmed',
    pending_candidate_registry_id: null,
    pending_since: null,
    display_name: displayName,
    status: assignment === null ? 'unassigned' : 'assigned',
    first_seen: bus === 'can' ? '2025-05-01T00:00:00Z' : '2025-06-01T00:00:00Z',
    last_seen: NOW,
    assignment,
  }
}

function bedSpot(bed: string, x: number, y: number): Assignment {
  return { kind: 'rs485', room: 'Flower Room', bed, x_frac: x, y_frac: y }
}

function seedRecords(): FixtureRecord[] {
  return [
    record(1, 'can', 1, 'Node 1', { kind: 'can', room: 'Flower Room', location_in_room: 'back' }),
    record(2, 'can', 2, 'Node 2', { kind: 'can', room: 'Flower Room', location_in_room: 'front' }),
    record(3, 'can', 3, 'Node 3', { kind: 'can', room: 'Veg Room', location_in_room: 'main' }),
    record(12, 'rs485', 2, 'soil_sensor_2', null),
    record(13, 'rs485', 3, 'soil_sensor_3', null),
    record(14, 'rs485', 4, 'soil_sensor_4', bedSpot('Back Bed', 0.3, 0.4)),
    record(15, 'rs485', 5, 'soil_sensor_5', bedSpot('Front Bed', 0.6, 0.5)),
    record(16, 'rs485', 6, 'soil_sensor_6', null),
  ]
}

const states = new Map<string, FixtureRecord[]>()

function stateFor(session: string): FixtureRecord[] {
  let records = states.get(session)
  if (records === undefined) {
    records = seedRecords()
    states.set(session, records)
  }
  return records
}

function json(status: number, body: unknown): SoilFixtureReply {
  return { status, body }
}

function apiError(status: number, message: string, errorCode: string | null): SoilFixtureReply {
  return json(status, { error: { status_code: status, message, error_code: errorCode } })
}

function setAssignment(target: FixtureRecord, assignment: Assignment | null): FixtureRecord {
  target.assignment = assignment
  target.status = assignment === null ? 'unassigned' : 'assigned'
  return target
}

function isFrac(value: unknown): value is number {
  return typeof value === 'number' && value >= 0 && value <= 1
}

function rs485Assignment(body: Record<string, unknown>): Assignment | string {
  const keys = Object.keys(body).filter(key => body[key] !== undefined && body[key] !== null)
  if (body.room === 'Veg Room') {
    return keys.every(key => key === 'kind' || key === 'room')
      ? { kind: 'rs485', room: 'Veg Room', bed: null, x_frac: null, y_frac: null }
      : 'Veg Room placement is room-level; bed, x_frac and y_frac are not allowed'
  }
  if (body.room !== 'Flower Room') return 'room must be Flower Room or Veg Room'
  if (
    (body.bed !== 'Back Bed' && body.bed !== 'Front Bed') ||
    !isFrac(body.x_frac) ||
    !isFrac(body.y_frac)
  ) {
    return 'Flower Room placement requires bed, x_frac and y_frac'
  }
  return bedSpot(body.bed, body.x_frac, body.y_frac)
}

function assignReply(target: FixtureRecord, rawBody: string | undefined): SoilFixtureReply {
  let body: Record<string, unknown>
  try {
    body = JSON.parse(rawBody ?? '{}') as Record<string, unknown>
  } catch {
    return apiError(422, 'Invalid JSON body', null)
  }
  if (body.kind === 'can' && target.bus === 'can') {
    if (typeof body.room !== 'string' || typeof body.location_in_room !== 'string') {
      return apiError(422, 'CAN assignment requires room and location_in_room', null)
    }
    return json(
      200,
      setAssignment(target, {
        kind: 'can',
        room: body.room,
        location_in_room: body.location_in_room,
      })
    )
  }
  if (body.kind !== 'rs485' || target.bus !== 'rs485') {
    return apiError(422, 'Assignment body does not match the registry bus', null)
  }
  const assignment = rs485Assignment(body)
  if (typeof assignment === 'string') return apiError(422, assignment, null)
  return json(200, setAssignment(target, assignment))
}

function heatmapReply(url: string): SoilFixtureReply {
  const metric = new URLSearchParams(url.split('?')[1] ?? '').get('metric') ?? 'water_content'
  const values = METRIC_VALUES[metric] ?? METRIC_VALUES.water_content
  const point = (
    registryId: number,
    address: number,
    x: number,
    y: number,
    removedAt: string | null
  ) => ({
    registry_id: registryId,
    hardware_address: address,
    x_frac: x,
    y_frac: y,
    value: values[address],
    active: removedAt === null,
    removed_at: removedAt,
  })
  const closedAt = new Date(Date.now() - 36 * HOUR_MS).toISOString()
  return json(200, {
    metric,
    stale_after_hours: 72,
    beds: [
      { ...BEDS[0], points: [point(14, 4, 0.3, 0.4, null), point(16, 6, 0.7, 0.7, closedAt)] },
      { ...BEDS[1], points: [point(15, 5, 0.6, 0.5, null)] },
    ],
  })
}

function liveReply(records: FixtureRecord[]): SoilFixtureReply {
  const probes = records
    .filter(
      item =>
        item.bus === 'rs485' && item.assignment?.kind === 'rs485' && item.assignment.bed !== null
    )
    .map(item => {
      const address = item.hardware_address
      const metric = (name: string) => ({
        value: METRIC_VALUES[name][address] ?? METRIC_VALUES[name][4],
        unit: SOIL_UNITS[name],
        observed_at: NOW,
        age_seconds: 1,
      })
      return {
        registry_id: item.registry_id,
        hardware_address: address,
        display_name: item.display_name,
        bed: item.assignment?.kind === 'rs485' ? item.assignment.bed : null,
        last_seen: NOW,
        metrics: {
          temperature: metric('temperature'),
          water_content: metric('water_content'),
          ec: metric('ec'),
          ph: metric('ph'),
        },
      }
    })
  return json(200, { generated_at: NOW, probes })
}

function historyReply(url: string): SoilFixtureReply {
  const search = new URLSearchParams(url.split('?')[1] ?? '')
  const startParam = search.get('start')
  const start = startParam === null ? new Date('2026-09-21T09:00:00Z') : new Date(startParam)
  const series = (['water_content', 'temperature', 'ec', 'ph'] as const).map((metric, slot) => {
    const address = slot % 2 === 0 ? 4 : 5
    return {
      registry_id: 10 + address,
      hardware_address: address,
      display_name: `soil_sensor_${address}`,
      bed: address === 4 ? 'Back Bed' : 'Front Bed',
      metric,
      unit: SOIL_UNITS[metric],
      points: Array.from({ length: 90 }, (_point, bucketIndex) => ({
        bucket_start: new Date(start.getTime() + bucketIndex * 120_000).toISOString(),
        average: 20 + (bucketIndex % 10) * 0.1,
        minimum: 19 + (bucketIndex % 5) * 0.1,
        maximum: 24 + (bucketIndex % 7) * 0.1,
        sample_count: 3,
      })),
    }
  })
  return json(200, {
    start: start.toISOString(),
    end: new Date(start.getTime() + 90 * 120_000).toISOString(),
    max_points: 100,
    tier: '1min',
    bucket_seconds: 120,
    series,
  })
}

/** Reply for a soil fixture route, or `null` when the request is not a soil route. */
export function soilFixtureReply(
  request: SoilFixtureRequest,
  scenario: string | null,
  session: string
): SoilFixtureReply | null {
  const pathname = request.url.split('?')[0]
  const method = request.method ?? 'GET'

  if (pathname === '/api/sensors/registry' && method === 'GET') {
    const search = new URLSearchParams(request.url.split('?')[1] ?? '')
    const bus = search.get('bus')
    const status = search.get('status')
    const records = stateFor(session).filter(
      item =>
        (bus === null || item.bus === bus) &&
        (status === null || status === 'all' || item.status === status)
    )
    return json(200, {
      records,
      unassigned_count: records.filter(item => item.status === 'unassigned').length,
    })
  }
  if (pathname === '/api/sensors/registry/rs485/bus-status' && method === 'GET') {
    return json(
      200,
      scenario === 'soil-power-cycle'
        ? { state: 'power_cycle_required', address: 3, at: NOW }
        : { state: 'ok', address: null, at: NOW }
    )
  }
  if (pathname === '/api/sensors/registry/rs485/beds' && method === 'GET') {
    return json(200, { beds: BEDS })
  }
  if (pathname === '/api/sensors/registry/rs485/heatmap' && method === 'GET') {
    return heatmapReply(request.url)
  }
  const assignment = /^\/api\/sensors\/registry\/(\d+)\/assignment$/.exec(pathname)
  if (assignment !== null && (method === 'PUT' || method === 'DELETE')) {
    const registryId = Number(assignment[1])
    const target = stateFor(session).find(item => item.registry_id === registryId)
    if (target === undefined) return apiError(404, `Registry record ${registryId} not found`, null)
    if (method === 'PUT') return assignReply(target, request.body)
    if (target.bus !== 'rs485') {
      return apiError(409, `Registry record ${registryId} is a CAN node`, 'not_rs485')
    }
    return json(200, setAssignment(target, null))
  }
  if (pathname === '/api/sensors/soil/live' && method === 'GET') {
    return liveReply(stateFor(session))
  }
  if (pathname === '/api/sensors/soil/history' && method === 'GET') {
    return historyReply(request.url)
  }
  return null
}
