import { describe, expect, it } from 'vitest'
import { RichTrajectoryEnvelope, type RichTrajectoryEnvelope as TrajectoryEnvelope } from '../api/contracts'
import { groupEnvelopeSegments } from '../charts/envelopeSeries'

const WINDOW = { start: '2026-01-01T00:00:00.000Z', end: '2026-01-02T00:00:00.000Z', timezone: 'UTC' }

describe('timeline owns the window edge', () => {
  it('holds the last scheduled value forward across the window end instead of a terminal null', () => {
    const envelope = RichTrajectoryEnvelope.parse({
      contract_version: 1,
      room: 'Flower Room',
      generated_at: '2026-01-01T00:00:00.000Z',
      window: WINDOW,
      revision_scope: 'saved',
      base_config_revision: 'config-7',
      draft_revision: null,
      segments: [{
        shape: 'step', value: 22,
        start: '2026-01-01T05:00:00.000Z', end: '2026-01-01T06:00:00.000Z',
        metric: 'heating_setpoint', unit: 'C',
        trajectory_kind: 'scheduled', quality: 'exact',
        source: { mode: 'flower', submode: null, period: { period_id: 'p1', label: 'Day' }, config_revision: 'config-7', draft_revision: null },
      }],
      assumptions: [],
      warnings: [],
    }) as TrajectoryEnvelope

    const groups = groupEnvelopeSegments(envelope)
    const lastStep = (groups[0].steps.at(-1) ?? { t: NaN, value: null })
    expect(lastStep.t).toBe(Date.parse('2026-01-01T05:00:00.000Z'))
    expect(lastStep.value).toBe(22)
  })
})
