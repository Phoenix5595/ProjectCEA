/**
 * Deterministic monitoring fixtures served by the preview middleware.
 *
 * These are read-only, deterministic JSON bodies that stand in for the real
 * sensor/control APIs during local browser QA. They are served on dedicated
 * paths under the exact configured loopback fixture origin (default `http://127.0.0.1:4187`) so a fixture
 * build never touches a production service. The response shapes mirror the
 * real sensor/control monitoring contracts so the native page renders with
 * data. Points are generated inside the requested `[start, end)` range so the
 * charts always have in-window data regardless of the live window.
 *
 * `MONITORING_SCENARIO=flower-partial` simulates a partial failure: the Front
 * live node returns empty and the control projection endpoint fails, while
 * Back live and recorded history still work.
 *
 * Budgeted sensor range reads (`max_points`, optional `interval_seconds`)
 * mirror the backend bucketing: one point per nice-ladder bucket labelled with
 * its start, including the in-progress bucket, with `interval_seconds` and
 * `requested_interval_seconds` metadata. `scenario=live-now` serves live values
 * whose timestamp follows the wall clock and whose value rises 0.1 per poll.
 */

const T0 = '2026-08-02T12:00:00.000Z'
const LIVE_FIXTURE_EPOCH = Date.now() - 120_000

/** Parse `start`/`end` query params from a request URL. */
export function parseRange(url: string): { start: string; end: string } {
  const q = url.split('?')[1] ?? ''
  const params = new URLSearchParams(q)
  const start = params.get('start') ?? '2026-08-02T09:00:00.000Z'
  const end = params.get('end') ?? T0
  return { start, end }
}

/** Monitoring-service nice interval ladder (`sensor_intervals.NICE_INTERVAL_SECONDS`). */
const NICE_INTERVAL_SECONDS = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400,
]

interface FixtureBudget {
  readonly maxPoints: number | null
  readonly requestedIntervalSeconds: number | null
}

/** Parse the R1 budget parameters (`max_points`, `interval_seconds`) from a request URL. */
export function parseBudget(url: string): FixtureBudget {
  const params = new URLSearchParams(url.split('?')[1] ?? '')
  const maxPoints = Number(params.get('max_points'))
  const interval = Number(params.get('interval_seconds'))
  return {
    maxPoints: Number.isInteger(maxPoints) && maxPoints > 0 ? maxPoints : null,
    requestedIntervalSeconds: Number.isInteger(interval) && interval > 0 ? interval : null,
  }
}

/** The backend's applied interval: the requested one, else the nicest fit for the budget. */
function appliedIntervalSeconds(start: string, end: string, budget: FixtureBudget): number | null {
  if (budget.requestedIntervalSeconds !== null) return budget.requestedIntervalSeconds
  if (budget.maxPoints === null) return null
  const durationSeconds = Math.max(1, (new Date(end).getTime() - new Date(start).getTime()) / 1000)
  const minimum = Math.ceil(durationSeconds / budget.maxPoints)
  return NICE_INTERVAL_SECONDS.find(interval => interval >= minimum) ?? 86400
}

function pointsInRange(
  start: string,
  end: string,
  values: number[],
  intervalSeconds: number | null
): Array<{
  timestamp: string
  average: number
  minimum: number
  maximum: number
  sample_count: number
}> {
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  if (values.length === 0) return []
  if (intervalSeconds === null || values.length > 2) {
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
  // Budgeted reads mirror the backend: one point per bucket, labelled with the
  // bucket start and including the in-progress bucket at the range end.
  const intervalMs = intervalSeconds * 1000
  const first = Math.floor(s / intervalMs) * intervalMs
  const from = values[0] ?? 0
  const to = values[values.length - 1] ?? from
  const points = []
  for (let t = first; t < e; t += intervalMs) {
    const fraction = e === first ? 0 : (t - first) / (e - first)
    const average = Math.round((from + (to - from) * fraction) * 1000) / 1000
    points.push({
      timestamp: new Date(t).toISOString(),
      average,
      minimum: average - 0.5,
      maximum: average + 0.5,
      sample_count: intervalSeconds,
    })
  }
  return points
}

function sensorSeries(
  sensor: string,
  node: string,
  unitFamily: string,
  unit: string,
  start: string,
  end: string,
  values: number[],
  intervalSeconds: number | null
) {
  return {
    sensor,
    node,
    unit_family: unitFamily,
    unit,
    points: pointsInRange(start, end, values, intervalSeconds),
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
  return Array.from(
    { length: 5100 },
    (_value, index) => values[index % values.length] + index / 50_000
  )
}

/** Canonical sensor series per room (Flower splits front/back; Veg is unsplit). */
function roomSensors(room: string): RoomSensor[] {
  if (room === 'Veg Room') {
    return [
      {
        sensor: 'dry_bulb_v',
        node: 'main',
        unitFamily: 'celsius',
        unit: '°C',
        values: [24.5, 24.8],
      },
      {
        sensor: 'wet_bulb_v',
        node: 'main',
        unitFamily: 'celsius',
        unit: '°C',
        values: [19.8, 20.1],
      },
      { sensor: 'rh_v', node: 'main', unitFamily: 'percent', unit: '%', values: [62, 61] },
      { sensor: 'vpd_v', node: 'main', unitFamily: 'kpa', unit: ' kPa', values: [1.1, 1.2] },
      { sensor: 'co2_v', node: 'main', unitFamily: 'ppm', unit: ' ppm', values: [620, 615] },
      {
        sensor: 'pressure_v',
        node: 'main',
        unitFamily: 'hpa',
        unit: ' hPa',
        values: [1013.2, 1013.1],
      },
      {
        sensor: 'secondary_temp_v',
        node: 'main',
        unitFamily: 'celsius',
        unit: '°C',
        values: [23.1, 23.4],
      },
      {
        sensor: 'secondary_rh_v',
        node: 'main',
        unitFamily: 'percent',
        unit: '%',
        values: [60, 59],
      },
      {
        sensor: 'water_level_v',
        node: 'main',
        unitFamily: 'mm',
        unit: ' mm',
        values: [45.0, 44.8],
      },
    ]
  }
  return [
    {
      sensor: 'dry_bulb_f',
      node: 'front',
      unitFamily: 'celsius',
      unit: '°C',
      values: [24.5, 24.8],
    },
    { sensor: 'dry_bulb_b', node: 'back', unitFamily: 'celsius', unit: '°C', values: [25.1, 25.4] },
    {
      sensor: 'wet_bulb_f',
      node: 'front',
      unitFamily: 'celsius',
      unit: '°C',
      values: [19.8, 20.1],
    },
    { sensor: 'wet_bulb_b', node: 'back', unitFamily: 'celsius', unit: '°C', values: [20.3, 20.6] },
    { sensor: 'rh_f', node: 'front', unitFamily: 'percent', unit: '%', values: [62, 61] },
    { sensor: 'rh_b', node: 'back', unitFamily: 'percent', unit: '%', values: [58, 57] },
    { sensor: 'vpd_f', node: 'front', unitFamily: 'kpa', unit: ' kPa', values: [1.1, 1.2] },
    { sensor: 'vpd_b', node: 'back', unitFamily: 'kpa', unit: ' kPa', values: [1.3, 1.4] },
    { sensor: 'co2_b', node: 'back', unitFamily: 'ppm', unit: ' ppm', values: [620, 615] },
    {
      sensor: 'pressure_b',
      node: 'back',
      unitFamily: 'hpa',
      unit: ' hPa',
      values: [1013.2, 1013.1],
    },
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
  budget: FixtureBudget = { maxPoints: null, requestedIntervalSeconds: null }
): unknown {
  const sensors = roomSensors(room)
  const intervalSeconds = appliedIntervalSeconds(start, end, budget)
  const tier =
    intervalSeconds === null || intervalSeconds < 60 ? 'raw' : intervalSeconds < 300 ? '1min' : '5min'
  return {
    metadata: {
      generated_at: new Date().toISOString(),
      tier,
      range: { start, end },
      room: { room, nodes: roomNodes(room) },
      ...(budget.maxPoints === null ? {} : { requested_max_points: budget.maxPoints }),
      ...(intervalSeconds === null ? {} : { interval_seconds: intervalSeconds }),
      requested_interval_seconds: budget.requestedIntervalSeconds,
    },
    series: sensors.map(s => {
      const values =
        _scenario === 'extreme-y' && s.sensor === 'dry_bulb_f'
          ? [24.5, 9, 36, 24.8]
          : _scenario === 'empty-bulb' && /^(?:dry_bulb|wet_bulb)_f$/.test(s.sensor)
            ? []
            : _scenario === 'empty-bulb' && s.sensor === 'dry_bulb_b'
              ? [0, ...s.values.slice(1)]
              : fixtureValues(s.values, _scenario)
      return sensorSeries(s.sensor, s.node, s.unitFamily, s.unit, start, end, values, intervalSeconds)
    }),
    statistics: sensors.map(s => statisticsFor(s.sensor, s.node, mean(s.values))),
  }
}
/** Guarded-preview payload with empty front bulb traces and populated back temperature/RH. */
export function emptyBulbSensorRangeFixture(room: string, start: string, end: string): unknown {
  return sensorRangeFixture(room, start, end, 'empty-bulb')
}

/** Current Redis values for one node as a `LiveSensorValue[]`. */
export function sensorLiveFixture(
  node: string,
  scenario: string | null = null,
  sequence = 0
): unknown {
  if (
    (process.env.MONITORING_SCENARIO === 'flower-partial' || scenario === 'flower-partial') &&
    node === 'front'
  ) {
    return []
  }
  if (scenario === 'live-update' && sequence >= 6 && sequence <= 7) return []
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
  // `live-now` models Redis values whose `_ts` advances with the wall clock.
  const timestamp =
    scenario === 'stale-live'
      ? '2026-01-01T00:00:00.000Z'
      : scenario === 'live-update'
        ? new Date(LIVE_FIXTURE_EPOCH + sequence * 1000).toISOString()
        : scenario === 'live-now'
          ? new Date(Math.floor(Date.now() / 1000) * 1000).toISOString()
          : T0
  const valueOffset = scenario === 'live-update' || scenario === 'live-now' ? sequence / 10 : 0
  return base.map(([name, value]) => ({
    sensor: `${name}_${suffix}`,
    value: Math.round((value + valueOffset) * 1000) / 1000,
    timestamp,
  }))
}


export {
  controlProjectionFixture,
  controlRangeFixture,
  controlTailFixture,
} from './fixtures.control.ts'

/** Deterministic WebSocket fixture message. */
export function wsFixtureMessage(): Record<string, unknown> {
  return {
    type: 'fixture',
    generated_at: T0,
    values: { dry_bulb: 24.6, rh: 62 },
  }
}
