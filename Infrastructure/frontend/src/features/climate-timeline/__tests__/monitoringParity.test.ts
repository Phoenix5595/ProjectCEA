import { describe, expect, it } from 'vitest'
import { RichTrajectoryEnvelope, type RichTrajectoryEnvelope as TrajectoryEnvelope } from '../api/contracts'
import { buildEnvelopeSeries, envelopeSampleTimes } from '../charts/envelopeSeries'
import { richProjectionTimeline } from '../../monitoring/state/monitoringStore.projection'
import type { NormStep, NormLinear } from '../../monitoring/data/alignSeries.types'
import { mergeControlSeries } from '../../monitoring/data/alignSeries.control'
import { alignLinear as monitoringAlignLinear, alignDeviceStates as monitoringAlignSteps } from '../../monitoring/data/alignSeries.series'
import type { NormControlSeries } from '../../monitoring/data/alignSeries.types'

const WINDOW = { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'UTC' }
const WINDOW_START = Date.parse(WINDOW.start)
const WINDOW_END = Date.parse(WINDOW.end)

const BASE_SOURCE = {
  mode: 'flower',
  submode: null,
  period: { period_id: 'p1', label: 'Day' },
  config_revision: 'config-7',
  draft_revision: null,
}

function scheduledSegment(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    shape: 'step',
    value: 22,
    start: WINDOW.start,
    end: WINDOW.end,
    metric: 'heating_setpoint',
    unit: 'C',
    trajectory_kind: 'scheduled',
    quality: 'exact',
    source: { ...BASE_SOURCE },
    ...overrides,
  }
}

const fixtureEnvelope = (): TrajectoryEnvelope => RichTrajectoryEnvelope.parse({
  contract_version: 1,
  room: 'Flower Room',
  generated_at: '2026-01-01T00:00:00.000Z',
  window: WINDOW,
  revision_scope: 'saved',
  base_config_revision: 'config-7',
  draft_revision: null,
  segments: [
    scheduledSegment({}),
    scheduledSegment({
      shape: 'linear', start_value: 22, end_value: 20,
      start: '2026-01-01T02:00:00.000Z', end: '2026-01-01T02:30:00.000Z',
    }),
    scheduledSegment({
      shape: 'step', value: 20,
      start: '2026-01-01T02:30:00.000Z', end: '2026-01-01T03:00:00.000Z',
    }),
    scheduledSegment({
      shape: 'unavailable', reason: 'schedule coverage is unavailable',
      start: '2026-01-01T03:00:00.000Z', end: '2026-01-01T03:30:00.000Z',
    }),
    scheduledSegment({
      shape: 'step', value: 19,
      start: '2026-01-01T03:30:00.000Z', end: '2026-01-01T04:00:00.000Z',
    }),
    scheduledSegment({ trajectory_kind: 'effective', value: 21 }),
    scheduledSegment({
      metric: 'cooling_setpoint', unit: 'C',
      shape: 'linear', start_value: 28, end_value: 26,
      start: WINDOW.start, end: '2026-01-01T01:00:00.000Z',
    }),
    scheduledSegment({
      metric: 'cooling_setpoint', unit: 'C',
      shape: 'step', value: 26,
      start: '2026-01-01T01:00:00.000Z', end: '2026-01-01T02:00:00.000Z',
    }),
    scheduledSegment({
      metric: 'vpd_setpoint', unit: 'kPa', value: 1.1,
      start: WINDOW.start, end: '2026-01-01T05:00:00.000Z',
    }),
    scheduledSegment({
      metric: 'co2_setpoint', unit: 'ppm', value: 900,
      start: WINDOW.start, end: '2026-01-01T05:00:00.000Z',
    }),
    scheduledSegment({
      metric: 'co2_setpoint', unit: 'ppm', value: 850,
      start: '2026-01-01T05:00:00.000Z', end: WINDOW.end,
    }),
  ],
  assumptions: [],
  warnings: [],
}) as TrajectoryEnvelope

function monitoringSeries(history: ReturnType<typeof richProjectionTimeline>['history']): Map<string, NormControlSeries> {
  const norm = mergeControlSeries(null, history)
  return new Map(norm.map((cs) => [`${cs.metric}:${cs.trajectoryKind}`, cs]))
}

function monitoringUnionValues(cs: NormControlSeries): (number | null)[] {
  const linear = monitoringAlignLinear(
    cs.linear.map((ln): NormLinear => ({ start: ln.start, end: ln.end, startValue: ln.startValue, endValue: ln.endValue, origin: 'projected', quality: 'estimated' })),
    x, 0, 0, false,
  )
  const steps = monitoringAlignSteps(
    cs.steps.map((st): NormStep => ({ t: st.t, value: st.value, origin: 'projected', quality: 'estimated' })),
    x, 0, 0, false,
  )
  return linear.map((value, index) => (value !== null && Number.isFinite(value) ? value : steps[index]))
}

const x = envelopeSampleTimes({ start: new Date(WINDOW_START), end: new Date(WINDOW_END) })

describe('monitoring/editor projection parity', () => {
  it('produces identical values at identical timestamps from the same fixture envelope', () => {
    const envelope = fixtureEnvelope()
    const editorSeries = buildEnvelopeSeries(envelope, x)

    const projection = richProjectionTimeline(envelope)
    expect(projection.history).not.toBeNull()
    const monitoring = monitoringSeries(projection.history)

    const monitorKeys = ['co2_setpoint:scheduled', 'cooling_setpoint:scheduled', 'heating_setpoint:effective', 'heating_setpoint:scheduled', 'vpd_setpoint:scheduled']
    expect([...monitoring.keys()].sort()).toEqual([...monitorKeys].sort())
    expect([...editorSeries.keys].sort()).toEqual([...monitorKeys].sort())

    const mismatches: string[] = []
    for (const key of [...editorSeries.series.keys()].sort()) {
      const cs = monitoring.get(key)
      if (!cs) {
        mismatches.push(`${key}: monitoring has no series`)
        continue
      }
      const expected = monitoringUnionValues(cs)
      const actual = editorSeries.series.get(key)
      if (!actual) {
        mismatches.push(`${key}: editor has no series`)
        continue
      }
      for (let index = 0; index < expected.length; index += 1) {
        if (expected[index] !== actual[index]) {
          mismatches.push(`${key}: first divergence at sample ${index} (t=${x[index]}) expected ${expected[index]} got ${actual[index]}`)
          break
        }
      }
    }
    if (mismatches.length > 0) throw new Error(`projection parity failed:\n${mismatches.join('\n')}`)
  })

  it('keeps the gap unbridged, step holds and ramp slopes on the shared grid', () => {
    const editorSeries = buildEnvelopeSeries(fixtureEnvelope(), x)
    const heating = editorSeries.series.get('heating_setpoint:scheduled') ?? []
    const hours = (n: number): number => (WINDOW_START + n * 3_600_000)
    const valueAt = (instant: number): number | null => {
      const index = x.indexOf(instant)
      return index === -1 ? null : heating[index]
    }
    expect(valueAt(hours(1))).toBe(22)
    expect(valueAt(hours(2) + 15 * 60_000)).toBe(21)
    expect(valueAt(hours(3) + 15 * 60_000)).toBeNull()
    expect(valueAt(hours(3) + 30 * 60_000)).toBe(19)
  })
})
