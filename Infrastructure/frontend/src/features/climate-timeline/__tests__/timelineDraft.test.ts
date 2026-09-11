import { describe, expect, it } from 'vitest'
import {
  createTimelineDraft,
  discardTimelineDraft,
  isTimelineDraftDirty,
  markTimelineDraftConflict,
  reviewTimelineDraft,
  updateTimelineDraftPeriods,
} from '../state/timelineDraft'
import type { ClimatePeriod } from '../../../types/climatePeriod'

const savedPeriod = (overrides: Partial<ClimatePeriod> = {}): ClimatePeriod => ({
  period_name: 'Day',
  start_time: '06:00',
  end_time: '18:00',
  ramp_minutes: 30,
  heating_setpoint: 22,
  cooling_setpoint: 25,
  vpd_setpoint: 1.2,
  co2_setpoint: 900,
  details: 'saved',
  ...overrides,
})

const savedBaseline = () => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: 'config-1',
  modeId: 17,
  submodeId: 3,
  window: {
    start: '2026-09-10T00:00:00.000Z',
    end: '2026-09-11T00:00:00.000Z',
    timezone: 'America/Toronto',
  },
  periods: [savedPeriod()],
  photoperiod: {
    dayStartTime: '06:00',
    nightStartTime: '18:00',
    rampUpMinutes: 20,
    rampDownMinutes: 20,
  },
})

describe('timeline draft state', () => {
  it('retains the saved request metadata used by review and Apply', () => {
    // Given: a saved baseline from the timeline API for a specific mode and window.
    const baseline = savedBaseline()

    // When: the editor creates its local draft.
    const draft = createTimelineDraft(baseline)

    // Then: review and Apply can retain the API's exact request identity.
    expect(draft.saved.modeId).toBe(17)
    expect(draft.saved.submodeId).toBe(3)
    expect(draft.saved.window).toEqual(baseline.window)
    expect(isTimelineDraftDirty(draft)).toBe(false)
  })

  it('routes a table-only period edit through review and leaves the saved baseline unchanged', () => {
    // Given: a saved room baseline and its shared table/graph draft.
    const initial = createTimelineDraft(savedBaseline())

    // When: the primary table replaces its controlled period row and the operator reviews it.
    const edited = updateTimelineDraftPeriods(initial, [savedPeriod({ heating_setpoint: 23.5 })])
    const reviewed = reviewTimelineDraft(edited)

    // Then: only the draft changes and review records its current revision.
    expect(reviewed.saved.periods[0]?.heating_setpoint).toBe(22)
    expect(reviewed.draft.periods[0]?.heating_setpoint).toBe(23.5)
    expect(reviewed.status).toEqual({ kind: 'reviewed', draftRevision: 1 })
  })

  it('discards table edits by restoring the saved baseline', () => {
    // Given: a dirty draft from a table-only edit.
    const dirty = updateTimelineDraftPeriods(
      createTimelineDraft(savedBaseline()),
      [savedPeriod({ period_name: 'Edited day' })],
    )

    // When: the operator discards it.
    const discarded = discardTimelineDraft(dirty)

    // Then: the editable values equal the saved baseline and are clean.
    expect(discarded.draft.periods).toEqual(discarded.saved.periods)
    expect(discarded.status).toEqual({ kind: 'editing' })
  })

  it('preserves the draft when a revision conflict is reported', () => {
    // Given: a reviewed draft awaiting Apply.
    const reviewed = reviewTimelineDraft(
      updateTimelineDraftPeriods(
        createTimelineDraft(savedBaseline()),
        [savedPeriod({ co2_setpoint: 1000 })],
      ),
    )

    // When: Apply receives an HTTP 409 conflict.
    const conflicted = markTimelineDraftConflict(reviewed)

    // Then: the operator's draft remains available for another review.
    expect(conflicted.draft.periods[0]?.co2_setpoint).toBe(1000)
    expect(conflicted.status).toEqual({ kind: 'conflict' })
  })
})
