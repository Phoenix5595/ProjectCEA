import { describe, expect, it } from 'vitest'

import type { ClimatePeriod } from '../../../types/climatePeriod'
import {
  applyTimelineWindowRead,
  createTimelineDraft,
  discardTimelineDraft,
  isTimelineDraftDirty,
  isTimelineDraftPersisted,
  markTimelineDraftConflict,
  reanchorTimelineSavedBaseline,
  reviewTimelineDraft,
  updateTimelineDraftPeriods,
} from '../state/timelineDraft'

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
  parametersConfigured: true,
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
    const dirty = updateTimelineDraftPeriods(createTimelineDraft(savedBaseline()), [
      savedPeriod({ period_name: 'Edited day' }),
    ])

    // When: the operator discards it.
    const discarded = discardTimelineDraft(dirty)

    // Then: the editable values equal the saved baseline and are clean.
    expect(discarded.draft.periods).toEqual(discarded.saved.periods)
    expect(discarded.status).toEqual({ kind: 'editing' })
  })

  it('preserves the draft when a revision conflict is reported', () => {
    // Given: a reviewed draft awaiting Apply.
    const reviewed = reviewTimelineDraft(
      updateTimelineDraftPeriods(createTimelineDraft(savedBaseline()), [
        savedPeriod({ co2_setpoint: 1000 }),
      ])
    )

    // When: Apply receives an HTTP 409 conflict.
    const conflicted = markTimelineDraftConflict(reviewed)

    // Then: the operator's draft remains available for another review.
    expect(conflicted.draft.periods[0]?.co2_setpoint).toBe(1000)
    expect(conflicted.status).toEqual({ kind: 'conflict' })
  })

  it('reports persisted matching values only for a configured clean draft', () => {
    // Given: a clean configured draft.
    const clean = createTimelineDraft(savedBaseline())

    // Then: Save has nothing to commit.
    expect(isTimelineDraftPersisted(clean)).toBe(true)

    // When: the operator edits and then reverts to the exact saved values.
    const edited = updateTimelineDraftPeriods(clean, [savedPeriod({ heating_setpoint: 23 })])
    const reverted = updateTimelineDraftPeriods(edited, [savedPeriod()])

    // Then: the reverted edit is still persisted matching; Save stays no-traffic.
    expect(isTimelineDraftDirty(reverted)).toBe(false)
    expect(isTimelineDraftPersisted(reverted)).toBe(true)

    // When: the baseline came back with unsaved initial defaults.
    const unconfigured = createTimelineDraft({ ...savedBaseline(), parametersConfigured: false })

    // Then: the unconfigured defaults still need Save even though values match.
    expect(isTimelineDraftPersisted(unconfigured)).toBe(false)
  })

  it('re-anchors saved authority on a late commit without touching the newer draft', () => {
    // Given: a draft whose operator edited while the commit was in flight.
    const dirty = updateTimelineDraftPeriods(createTimelineDraft(savedBaseline()), [
      savedPeriod({ heating_setpoint: 25 }),
    ])
    const committed = {
      ...savedBaseline(),
      baseConfigRevision: '0000002',
      periods: [savedPeriod({ heating_setpoint: 23, details: 'committed' })],
    }

    // When: the late commit is reconciled into the draft state.
    const reanchored = reanchorTimelineSavedBaseline(dirty, committed)

    // Then: the saved authority is the committed baseline, the newer edit
    // survives as a dirty draft, and no reviewed claim is retained.
    expect(reanchored.saved.periods[0]?.heating_setpoint).toBe(23)
    expect(reanchored.draft.periods[0]?.heating_setpoint).toBe(25)
    expect(isTimelineDraftDirty(reanchored)).toBe(true)
    expect(reanchored.status).toEqual({ kind: 'editing' })
  })

  describe('applyTimelineWindowRead', () => {
    const windowB = {
      start: '2026-01-02T00:00:00.000Z',
      end: '2026-01-03T00:00:00.000Z',
      timezone: 'UTC',
    }
    const readBaseline = (overrides: Partial<Parameters<typeof createTimelineDraft>[0]> = {}) => ({
      ...savedBaseline(),
      window: windowB,
      trajectory: undefined,
      ...overrides,
    })

    it('updates only window and trajectory metadata for an equal saved revision', () => {
      // Given: a dirty draft and a same-revision window read without an envelope.
      const dirty = updateTimelineDraftPeriods(createTimelineDraft(savedBaseline()), [
        savedPeriod({ heating_setpoint: 23 }),
      ])

      // When: the window read resolves with the same revision.
      const result = applyTimelineWindowRead(dirty, readBaseline())

      // Then: values and the expected revision stay; only the window moves and
      // the old-window envelope is not carried across.
      expect(result.kind).toBe('same-revision')
      expect(result.state.saved.baseConfigRevision).toBe('config-1')
      expect(result.state.saved.window).toEqual(windowB)
      expect(result.state.saved.trajectory).toBeUndefined()
      expect(result.state.draft.periods[0]?.heating_setpoint).toBe(23)
      expect(result.state.status).toEqual(dirty.status)
    })

    it('replaces the complete baseline for a different revision with a clean draft', () => {
      // Given: a clean draft and a newer authority for the new window.
      const clean = createTimelineDraft(savedBaseline())
      const newer = readBaseline({
        baseConfigRevision: '0000002',
        periods: [savedPeriod({ heating_setpoint: 21 })],
      })

      // When: the window read resolves.
      const result = applyTimelineWindowRead(clean, newer)

      // Then: the baseline, draft values, revision and window all re-anchor;
      // the read carried no envelope so none is adopted.
      expect(result.kind).toBe('rebased')
      expect(result.state.saved.baseConfigRevision).toBe('0000002')
      expect(result.state.draft.periods[0]?.heating_setpoint).toBe(21)
      expect(result.state.saved.window).toEqual(windowB)
      expect(result.state.saved.trajectory).toBeUndefined()
      expect(result.state.status).toEqual({ kind: 'editing' })
    })

    it('keeps every draft value and the old revision while marking a conflict', () => {
      // Given: a dirty draft and a newer authority read for the new window.
      const dirty = updateTimelineDraftPeriods(createTimelineDraft(savedBaseline()), [
        savedPeriod({ heating_setpoint: 23 }),
      ])

      // When: the window read resolves with a different revision.
      const result = applyTimelineWindowRead(
        dirty,
        readBaseline({ baseConfigRevision: '0000002' })
      )

      // Then: no dirty rebase happens; the draft and old expected revision are
      // retained, the conflict is visible, and the operator's chosen window
      // still governs the next request.
      expect(result.kind).toBe('conflict')
      expect(result.state.draft.periods[0]?.heating_setpoint).toBe(23)
      expect(result.state.saved.baseConfigRevision).toBe('config-1')
      expect(result.state.saved.window).toEqual(windowB)
      expect(result.state.saved.trajectory).toBeUndefined()
      expect(result.state.status).toEqual({ kind: 'conflict' })
    })
  })
})
