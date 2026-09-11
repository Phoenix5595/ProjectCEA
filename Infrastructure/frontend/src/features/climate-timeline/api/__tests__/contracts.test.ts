import { describe, expect, it } from 'vitest'
import { RichTrajectoryEnvelope } from '../contracts'

const linearSegment = () => ({
  shape: 'linear' as const,
  start: '2026-01-01T00:00:00.000Z',
  end: '2026-01-01T00:30:00.000Z',
  start_value: 20,
  end_value: 22,
  metric: 'temperature',
  unit: 'celsius',
  trajectory_kind: 'scheduled' as const,
  quality: 'exact' as const,
  source: {
    mode: 'flower',
    submode: 'stretch',
    period: { period_id: 'period-1', label: 'Morning' },
    config_revision: 'cfg-7',
    draft_revision: 'draft-4',
  },
})

const envelope = () => ({
  contract_version: 1 as const,
  room: 'Flower Room',
  generated_at: '2026-01-01T00:00:00.000Z',
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-01T01:00:00.000Z',
    timezone: 'America/Toronto',
  },
  revision_scope: 'draft' as const,
  base_config_revision: 'cfg-7',
  draft_revision: 'draft-4',
  segments: [linearSegment()],
  assumptions: ['Assumes override remains active'],
  warnings: [],
})

describe('RichTrajectoryEnvelope', () => {
  it('accepts a linear UTC trajectory segment', () => {
    // Given: a draft trajectory with a half-open linear ramp.
    const payload = envelope()

    // When: it enters the frontend API boundary.
    const parsed = RichTrajectoryEnvelope.safeParse(payload)

    // Then: its discriminated shape and UTC window parse successfully.
    expect(parsed.success).toBe(true)
  })

  it('rejects a reversed segment interval', () => {
    // Given: an end timestamp earlier than the linear segment start.
    const payload = envelope()
    payload.segments[0].end = '2025-12-31T23:59:00.000Z'

    // When: it enters the frontend API boundary.
    const parsed = RichTrajectoryEnvelope.safeParse(payload)

    // Then: the half-open interval invariant rejects it.
    expect(parsed.success).toBe(false)
  })

  it('rejects mixed base and draft revisions', () => {
    // Given: a draft envelope with a segment from a different draft.
    const payload = envelope()
    payload.segments[0].source.draft_revision = 'draft-5'

    // When: it enters the frontend API boundary.
    const parsed = RichTrajectoryEnvelope.safeParse(payload)

    // Then: aggregate revision identity rejects the mixed source.
    expect(parsed.success).toBe(false)
  })

  it.each(['start_value', 'end_value'] as const)(
    'rejects nonfinite linear %s',
    (field) => {
      // Given: a linear endpoint whose number is non-finite.
      const payload = envelope()
      payload.segments[0][field] = Number.POSITIVE_INFINITY

      // When: it enters the frontend API boundary.
      const parsed = RichTrajectoryEnvelope.safeParse(payload)

      // Then: the finite numeric contract rejects it.
      expect(parsed.success).toBe(false)
    },
  )
})
