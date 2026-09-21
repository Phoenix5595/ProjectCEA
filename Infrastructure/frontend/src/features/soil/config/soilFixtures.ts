/**
 * Deterministic soil fixtures for the monitoring preview: registry, live
 * soil, assignment, and soil-history responses keyed by scenario. Sessions
 * stay isolated through the `fixtureSession` query param (handled by the
 * preview middleware), and every URL stays on the fixture origin.
 */
interface FixtureRequest {
  url?: string
  method?: string
  body?: string
}

/** Fixture-route contract mirroring vite.monitoring.config.ts. */
interface FixtureRoute {
  re: RegExp
  handler: (req: FixtureRequest, scenario: string | null) => unknown
}

const NOW = '2026-09-21T12:00:00Z'

const SOIL_UNITS: Record<string, string> = {
  temperature: '°C',
  water_content: '%',
  ec: 'µS/cm',
  ph: 'pH',
}

function probeCountFrom(scenario: string | null): number {
  const match = /^soil-probes-(0|1|2|3|4)$/.exec(scenario ?? '')
  return match === null ? 2 : Number(match[1])
}

function rangeStartFrom(url: string): Date {
  const search = new URLSearchParams(url.split('?')[1] ?? '')
  const start = search.get('start')
  return start === null ? new Date('2026-09-21T09:00:00Z') : new Date(start)
}

function canEntry(
  registryId: number,
  hardwareAddress: number,
  displayName: string,
  room: string,
  location: string,
): Record<string, unknown> {
  return {
    registry_id: registryId,
    bus: 'can',
    hardware_address: hardwareAddress,
    display_name: displayName,
    status: 'assigned',
    first_seen: '2025-05-01T00:00:00Z',
    last_seen: NOW,
    assignment: { kind: 'can', room, location_in_room: location },
  }
}

function rs485Entry(
  registryId: number,
  hardwareAddress: number,
  bed: string | null,
): Record<string, unknown> {
  return {
    registry_id: registryId,
    bus: 'rs485',
    hardware_address: hardwareAddress,
    display_name: `soil_sensor_${hardwareAddress}`,
    status: bed === null ? 'unassigned' : 'assigned',
    first_seen: '2025-06-01T00:00:00Z',
    last_seen: NOW,
    assignment: bed === null ? null : { kind: 'rs485', room: 'Flower Room', bed },
  }
}

function soilRegistryFixture(_req: FixtureRequest, scenario: string | null): unknown {
  const records: Array<Record<string, unknown>> = [
    canEntry(1, 1, 'Node 1', 'Flower Room', 'back'),
    canEntry(2, 2, 'Node 2', 'Flower Room', 'front'),
    canEntry(3, 3, 'Node 3', 'Veg Room', 'main'),
    rs485Entry(4, 226, 'Front Bed'),
    rs485Entry(5, 227, 'Back Bed'),
  ]
  if (probeCountFrom(scenario) > 1) {
    records.push(rs485Entry(6, 228, 'Front Bed'))
  }
  if (scenario === 'unassigned-after-mount') {
    records.push(rs485Entry(9, 231, null))
  }
  const unassigned = records.filter((record) => record.status === 'unassigned')
  return { records, unassigned_count: unassigned.length }
}

function soilLiveFixture(_req: FixtureRequest, scenario: string | null): unknown {
  const stale = scenario === 'soil-stale'
  const count = Math.max(Math.min(probeCountFrom(scenario), 4), stale ? 1 : 0)
  const probes = Array.from({ length: count }, (_value, index) => {
    const hardwareAddress = 226 + index
    return {
      registry_id: 4 + index,
      hardware_address: hardwareAddress,
      display_name: `soil_sensor_${hardwareAddress}`,
      bed: index % 2 === 0 ? 'Front Bed' : 'Back Bed',
      last_seen: NOW,
      metrics:
        stale && index === 0
          ? {
              temperature: { value: 21.4, unit: '°C', observed_at: NOW, age_seconds: 1 },
              water_content: null,
              ec: { value: 1180.0, observed_at: NOW, age_seconds: 1, unit: 'µS/cm' },
              ph: null,
            }
          : {
              temperature: { value: 21.4, observed_at: NOW, age_seconds: 1, unit: '°C' },
              water_content: { value: 32.1, observed_at: NOW, age_seconds: 1, unit: '%' },
              ec: { value: 1180.0, observed_at: NOW, age_seconds: 1, unit: 'µS/cm' },
              ph: { value: 6.6, observed_at: NOW, age_seconds: 1, unit: 'pH' },
            },
    }
  })
  return { generated_at: NOW, probes }
}

function soilHistoryFixture(req: FixtureRequest, scenario: string | null): unknown {
  const seriesCount = scenario === 'soil-32-series' ? 32 : 4
  const start = rangeStartFrom(req.url ?? '')
  const series = Array.from({ length: seriesCount }, (_value, index) => {
    const slot = index % 4
    const metric = (['water_content', 'temperature', 'ec', 'ph'] as const)[slot] ?? 'water_content'
    const hardwareAddress = 226 + slot
    return {
      registry_id: 4 + slot,
      hardware_address: hardwareAddress,
      display_name: `soil_sensor_${hardwareAddress}`,
      bed: slot % 2 === 0 ? 'Front Bed' : 'Back Bed',
      metric,
      unit: SOIL_UNITS[metric] ?? '%',
      points: Array.from({ length: 90 }, (_point, bucketIndex) => ({
        bucket_start: new Date(start.getTime() + bucketIndex * 120_000).toISOString(),
        average: 20 + (bucketIndex % 10) * 0.1,
        minimum: 19 + (bucketIndex % 5) * 0.1,
        maximum: 24 + (bucketIndex % 7) * 0.1,
        sample_count: 3,
      })),
    }
  })
  return {
    start: start.toISOString(),
    end: new Date(start.getTime() + 90 * 120_000).toISOString(),
    max_points: 100,
    tier: '1min',
    bucket_seconds: 120,
    series,
  }
}

function soilAssignmentFixture(req: FixtureRequest, scenario: string | null): unknown {
  if (scenario === 'bed-capacity-conflict') {
    return {
      error: {
        status_code: 409,
        message: 'Front Bed already holds 4 probes and is at capacity',
        error_code: 'bed_capacity',
      },
    }
  }
  const parsed = JSON.parse(req.body ?? '{}') as {
    kind?: string
    bed?: string
    room?: string
    location_in_room?: string
  }
  const registryId = registryIdFromUrl(req.url ?? '')
  return {
    registry_id: registryId,
    bus: parsed.kind ?? 'rs485',
    hardware_address: 231,
    display_name: 'soil_sensor_231',
    status: 'assigned',
    first_seen: NOW,
    last_seen: NOW,
    assignment:
      parsed.kind === 'can'
        ? {
            kind: 'can',
            room: parsed.room ?? 'Flower Room',
            location_in_room: parsed.location_in_room ?? 'main',
          }
        : { kind: 'rs485', room: 'Flower Room', bed: parsed.bed ?? 'Front Bed' },
  }
}

function registryIdFromUrl(url: string): number {
  const parts = url.split('?')[0].split('/').filter(Boolean)
  return Number(parts.at(-2) ?? 7)
}

export const SOIL_FIXTURE_ROUTES: FixtureRoute[] = [
  { re: /^\/api\/sensors\/registry$/, handler: soilRegistryFixture },
  { re: /^\/api\/sensors\/registry\/[^/]+\/assignment$/, handler: soilAssignmentFixture },
  { re: /^\/api\/sensors\/soil\/live$/, handler: soilLiveFixture },
  { re: /^\/api\/sensors\/soil\/history$/, handler: soilHistoryFixture },
]
