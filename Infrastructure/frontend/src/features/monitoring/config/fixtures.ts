/**
 * Deterministic monitoring fixtures served by the preview middleware.
 *
 * These are read-only, deterministic JSON bodies that stand in for the real
 * sensor/control APIs during local browser QA. They are served on dedicated
 * paths under the exact fixture origin (`http://127.0.0.1:4173`) so a fixture
 * build never touches a production service. The response shapes mirror the
 * real sensor/control monitoring contracts so the native page renders with
 * data. Points are generated inside the requested `[start, end)` range so the
 * charts always have in-window data regardless of the live window.
 *
 * `MONITORING_SCENARIO=flower-partial` simulates a partial failure: the Front
 * live node returns empty and the control projection endpoint fails, while
 * Back live and recorded history still work.
 */

const T0 = '2026-08-02T12:00:00.000Z'

/** Parse `start`/`end` query params from a request URL. */
export function parseRange(url: string): { start: string; end: string } {
  const q = url.split('?')[1] ?? ''
  const params = new URLSearchParams(q)
  const start = params.get('start') ?? '2026-08-02T09:00:00.000Z'
  const end = params.get('end') ?? T0
  return { start, end }
}

function pointsInRange(
  start: string,
  end: string,
  values: number[],
): Array<{ timestamp: string; average: number; minimum: number; maximum: number; sample_count: number }> {
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  const n = Math.max(2, values.length)
  return values.map((v, i) => {
    const t = s + Math.round(((e - s) * i) / (n - 1))
    return {
      timestamp: new Date(t).toISOString(),
      average: v,
      minimum: v - 0.5,
      maximum: v + 0.5,
      sample_count: 60,
    }
  })
}

function sensorSeries(
  sensor: string,
  node: string,
  unitFamily: string,
  unit: string,
  start: string,
  end: string,
  values: number[],
) {
  return {
    sensor,
    node,
    unit_family: unitFamily,
    unit,
    points: pointsInRange(start, end, values),
  }
}

function statisticsFor(sensor: string, node: string, average: number) {
  return {
    sensor,
    node,
    minimum: average - 0.5,
    maximum: average + 0.5,
    average,
    stddev_samp: 0.2,
    sample_count: 3600,
  }
}

interface RoomSensor {
  sensor: string
  node: string
  unitFamily: string
  unit: string
  values: number[]
}

function mean(values: number[]): number {
  return values.reduce((a, b) => a + b, 0) / values.length
}

function fixtureValues(values: number[], scenario: string | null): number[] {
  if (scenario !== 'large-x') return values
  return Array.from({ length: 5100 }, (_value, index) => values[index % values.length] + index / 50_000)
}

/** Canonical sensor series per room (Flower splits front/back; Veg is unsplit). */
function roomSensors(room: string): RoomSensor[] {
  if (room === 'Veg Room') {
    return [
      { sensor: 'dry_bulb_v', node: 'main', unitFamily: 'celsius', unit: '°C', values: [24.5, 24.8] },
      { sensor: 'wet_bulb_v', node: 'main', unitFamily: 'celsius', unit: '°C', values: [19.8, 20.1] },
      { sensor: 'rh_v', node: 'main', unitFamily: 'percent', unit: '%', values: [62, 61] },
      { sensor: 'vpd_v', node: 'main', unitFamily: 'kpa', unit: ' kPa', values: [1.1, 1.2] },
      { sensor: 'co2_v', node: 'main', unitFamily: 'ppm', unit: ' ppm', values: [620, 615] },
      { sensor: 'pressure_v', node: 'main', unitFamily: 'hpa', unit: ' hPa', values: [1013.2, 1013.1] },
      { sensor: 'secondary_temp_v', node: 'main', unitFamily: 'celsius', unit: '°C', values: [23.1, 23.4] },
      { sensor: 'secondary_rh_v', node: 'main', unitFamily: 'percent', unit: '%', values: [60, 59] },
      { sensor: 'water_level_v', node: 'main', unitFamily: 'mm', unit: ' mm', values: [45.0, 44.8] },
    ]
  }
  return [
    { sensor: 'dry_bulb_f', node: 'front', unitFamily: 'celsius', unit: '°C', values: [24.5, 24.8] },
    { sensor: 'dry_bulb_b', node: 'back', unitFamily: 'celsius', unit: '°C', values: [25.1, 25.4] },
    { sensor: 'rh_f', node: 'front', unitFamily: 'percent', unit: '%', values: [62, 61] },
    { sensor: 'rh_b', node: 'back', unitFamily: 'percent', unit: '%', values: [58, 57] },
    { sensor: 'vpd_f', node: 'front', unitFamily: 'kpa', unit: ' kPa', values: [1.1, 1.2] },
    { sensor: 'vpd_b', node: 'back', unitFamily: 'kpa', unit: ' kPa', values: [1.3, 1.4] },
    { sensor: 'co2_b', node: 'back', unitFamily: 'ppm', unit: ' ppm', values: [620, 615] },
    { sensor: 'pressure_b', node: 'back', unitFamily: 'hpa', unit: ' hPa', values: [1013.2, 1013.1] },
  ]
}

function roomNodes(room: string): string[] {
  return room === 'Veg Room' ? ['main'] : ['front', 'back']
}

/** Tiered historical envelopes and exact statistics for one room. */
export function sensorRangeFixture(
  room: string,
  start: string,
  end: string,
  _scenario: string | null = null,
): unknown {
  const sensors = roomSensors(room)
  return {
    metadata: {
      generated_at: T0,
      tier: 'raw',
      range: { start, end },
      room: { room, nodes: roomNodes(room) },
    },
    series: sensors.map((s) =>
      sensorSeries(s.sensor, s.node, s.unitFamily, s.unit, start, end, fixtureValues(s.values, _scenario)),
    ),
    statistics: sensors.map((s) => statisticsFor(s.sensor, s.node, mean(s.values))),
  }
}

/** Current Redis values for one node as a `LiveSensorValue[]`. */
export function sensorLiveFixture(
  node: string,
  scenario: string | null = null,
): unknown {
  if (process.env.MONITORING_SCENARIO === 'flower-partial' && node === 'front') {
    return []
  }
  const suffix = node === 'front' ? 'f' : node === 'back' ? 'b' : 'v'
  const base: Array<[string, number]> = [
    ['dry_bulb', 24.6],
    ['wet_bulb', 19.8],
    ['rh', 62],
    ['vpd', 1.1],
    ['co2', 620],
    ['pressure', 1013.2],
    ['water_level', 45.0],
  ]
  const timestamp = scenario === 'stale-live' ? '2026-01-01T00:00:00.000Z' : T0
  return base.map(([name, value]) => ({
    sensor: `${name}_${suffix}`,
    value,
    timestamp,
  }))
}

/** Exact moving-range statistics without loading series envelopes. */
export function sensorStatsFixture(
  room: string,
  start: string,
  end: string,
  _scenario: string | null = null,
): unknown {
  const sensors = roomSensors(room)
  return {
    metadata: {
      generated_at: T0,
      tier: 'raw',
      range: { start, end },
      room: { room, nodes: roomNodes(room) },
    },
    series: [],
    statistics: sensors.map((s) => statisticsFor(s.sensor, s.node, mean(s.values))),
  }
}

export {
  controlProjectionFixture,
  controlRangeFixture,
} from './fixtures.control'

/** Grafana placeholder body for the fixture origin's `/grafana/*` paths. */
export function grafanaPlaceholder(): Record<string, unknown> {
  return {
    service: 'grafana-placeholder',
    message: 'Grafana is not reachable from the fixture build; native monitoring is used.',
  }
}

/** Deterministic WebSocket fixture message. */
export function wsFixtureMessage(): Record<string, unknown> {
  return {
    type: 'fixture',
    generated_at: T0,
    values: { dry_bulb: 24.6, rh: 62 },
  }
}
