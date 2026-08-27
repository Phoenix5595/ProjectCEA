/**
 * Deterministic control-monitoring fixtures served by the preview middleware.
 *
 * Split from `fixtures.ts` to keep each module under the 250-LOC ceiling. These
 * mirror the automation `/api/monitoring/control/*` response contracts so the
 * native monitoring pages render recorded/projected control history.
 */

const T0 = '2026-08-02T12:00:00.000Z'

function provenance(origin: string, quality: string) {
  return { origin, quality, is_aggregated: false }
}

function climateSeries(name: string, metric: string, start: string, end: string, value: number) {
  return {
    name,
    provenance: provenance('recorded', 'exact'),
    projection: null,
    warnings: [],
    points: [
      {
        timestamp: start,
        value,
        nominal_value: value,
        metric,
        provenance: provenance('recorded', 'exact'),
      },
      {
        timestamp: end,
        value,
        nominal_value: value,
        metric,
        provenance: provenance('recorded', 'exact'),
      },
    ],
    steps: [],
    linear: [],
  }
}

interface ClimateFixtureSeries {
  name: string
  provenance: Record<string, unknown>
  projection: Record<string, unknown> | null
  warnings: unknown[]
  points: Array<Record<string, unknown>>
  steps: unknown[]
  linear: unknown[]
}

interface ControlFixture {
  range: { start: string; end: string }
  runtime_snapshot_version: number
  cursors: Array<Record<string, unknown>>
  flush_health: Array<Record<string, unknown>>
  climate: ClimateFixtureSeries[]
  lights: unknown[]
  devices: unknown[]
  pid: unknown[]
  photoperiod: unknown[]
}

function controlBase(start: string, end: string): ControlFixture {
  return {
    range: { start, end },
    runtime_snapshot_version: 7,
    cursors: [
      { source: 'effective_setpoints', cursor: '42', has_more: false },
      { source: 'automation_state', cursor: '10', has_more: false },
      { source: 'photoperiod_history', cursor: '3', has_more: false },
    ],
    flush_health: [
      { source: 'photoperiod_history', dropped_rows: 0, last_flushed_at: T0, healthy: true },
    ],
    climate: [
      climateSeries('heating', 'heating_setpoint', start, end, 22),
      climateSeries('cooling', 'cooling_setpoint', start, end, 27),
      climateSeries('vpd', 'vpd_setpoint', start, end, 1.2),
    ],
    lights: [],
    devices: [],
    pid: [],
    photoperiod: [],
  }
}

/** One atomic historical range and per-source tail high-water marks. */
export function controlRangeFixture(
  _room: string,
  start: string,
  end: string,
  scenario: string | null = null,
): unknown {
  const base = controlBase(start, end)
  if (scenario === 'delayed-control-recovery') {
    return {
      ...base,
      flush_health: base.flush_health.map((health) => ({
        ...health,
        dropped_rows: 1,
        healthy: false,
      })),
    }
  }
  if (scenario === 'unknown-photoperiod') {
    return {
      ...base,
      photoperiod: [
        {
          timestamp: start,
          phase: 'UNKNOWN',
          provenance: provenance('recorded', 'unavailable'),
        },
      ],
    }
  }
  return base
}

export function controlTailFixture(
  room: string,
  start: string,
  end: string,
  scenario: string | null = null,
): unknown {
  return controlRangeFixture(
    room,
    start,
    end,
    scenario === 'delayed-control-recovery' ? null : scenario,
  )
}

/** Future-only climate, light, and photoperiod projections. */
export function controlProjectionFixture(
  _room: string,
  start: string,
  end: string,
  scenario: string | null = null,
): unknown {
  const base = controlBase(start, end)
  const partial = process.env.MONITORING_SCENARIO === 'flower-partial'
  const missing = scenario === 'missing-projection'
  if (partial || missing) return { quality: 'unavailable', value: [] }

  return {
    quality: 'estimated',
    value: [
      {
        version: { contract_version: 1, config_version: 7, revision: 'f1c7a11' },
        generated_at: T0,
        valid_from: start,
        valid_until: end,
        series: base.climate.map((s) => ({
          series_id: { value: `climate.${s.points[0]?.metric}_target` },
          value: s.points[0]?.value ?? null,
          quality: 'estimated',
          valid_from: start,
          valid_until: end,
        })),
      },
    ],
  }
}
