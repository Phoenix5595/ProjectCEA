import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { createClimatePeriodsTableAdapter } from '../adapters/climatePeriodsTableAdapter'
import { RichTrajectoryEnvelope } from '../api/contracts'
import { TimelineConflictError, type TimelinePublicationPort } from '../api/timelinePublicationPort'
import { useTimelineDraft } from '../state/useTimelineDraft'
import type { TimelineSavedBaseline } from '../state/timelineDraft'

const savedBaseline = (revision = 'config-1'): TimelineSavedBaseline => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: revision,
  modeId: 17,
  submodeId: 3,
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    timezone: 'America/Toronto',
  },
  periods: [{
    period_name: 'Day',
    start_time: '06:00',
    end_time: '18:00',
    ramp_minutes: 30,
    heating_setpoint: 22,
    cooling_setpoint: 25,
    vpd_setpoint: 1.2,
    co2_setpoint: 900,
    details: 'saved',
  }],
  photoperiod: {
    dayStartTime: '06:00',
    nightStartTime: '18:00',
    rampUpMinutes: 20,
    rampDownMinutes: 20,
  },
  trajectory: previewEnvelope(0),
})

function previewEnvelope(draftRevision: number) {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Flower Room',
    generated_at: '2026-01-01T00:00:00.000Z',
    window: {
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-01T01:00:00.000Z',
      timezone: 'America/Toronto',
    },
    revision_scope: 'draft',
    base_config_revision: 'config-1',
    draft_revision: `draft-${draftRevision}`,
    segments: [{
      shape: 'step',
      value: 22,
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-01T01:00:00.000Z',
      metric: 'temperature',
      unit: 'celsius',
      trajectory_kind: 'scheduled',
      quality: 'exact',
      source: {
        mode: 'flower',
        submode: null,
        period: { period_id: 'day', label: 'Day' },
        config_revision: 'config-1',
        draft_revision: `draft-${draftRevision}`,
      },
    }],
    assumptions: [],
    warnings: [],
  })
}

function successfulPort(): TimelinePublicationPort {
  return {
    preview: async (request) => previewEnvelope(request.draftRevision),
    apply: async (request) => ({
      room: request.room,
      baseConfigRevision: 'config-2',
      ...request.values,
    }),
  }
}

describe('useTimelineDraft', () => {
  it('forwards the saved mode and window metadata when reviewing a draft', async () => {
    let requestedModeId: number | undefined
    let requestedSubmodeId: number | null | undefined
    let requestedWindow: TimelineSavedBaseline['window']
    const port: TimelinePublicationPort = {
      preview: async (request) => {
        requestedModeId = request.modeId
        requestedSubmodeId = request.submodeId
        requestedWindow = request.window
        return previewEnvelope(request.draftRevision)
      },
      apply: async () => savedBaseline(),
    }
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))

    await act(async () => result.current.review())

    expect(requestedModeId).toBe(17)
    expect(requestedSubmodeId).toBe(3)
    expect(requestedWindow).toEqual(savedBaseline().window)
    expect(result.current.state.saved.trajectory).toEqual(previewEnvelope(0))
  })

  it('reviews and applies a primary-table-only edit without applying light settings', async () => {
    // Given: a shared draft exposed to the existing controlled primary table.
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: successfulPort() }))
    const table = createClimatePeriodsTableAdapter(result.current.state, result.current.editPeriods)

    // When: the table changes climate periods, then the operator reviews and applies the draft.
    act(() => table.onChange(table.periods.map((period) => ({ ...period, heating_setpoint: 23.5 }))))
    await act(async () => result.current.review())
    await act(async () => result.current.apply())

    // Then: the saved timeline baseline advances and its aggregate contains only timeline-owned values.
    expect(result.current.state.saved.periods[0]?.heating_setpoint).toBe(23.5)
    expect(result.current.state.saved.baseConfigRevision).toBe('config-2')
    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })

  it('warns before a dirty room switch and keeps the original draft until confirmed', () => {
    // Given: an edited Flower draft.
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: successfulPort() }))
    act(() => result.current.editPeriods(result.current.state.draft.periods.map((period) => ({ ...period, details: 'unsaved' }))))
    const vegetation = { ...savedBaseline(), room: { location: 'vegetation', cluster: 'main' } }

    // When: navigation requests another room before the operator discards the draft.
    let switchResult: string = ''
    act(() => { switchResult = result.current.switchRoom(vegetation) })

    // Then: the original dirty draft remains in place until explicit confirmation.
    expect(switchResult).toBe('requires-discard')
    expect(result.current.state.saved.room.location).toBe('flower')
    expect(result.current.state.draft.periods[0]?.details).toBe('unsaved')
  })

  it('preserves the reviewed draft after Apply receives a revision conflict', async () => {
    // Given: a reviewed timeline draft and a revision-checked publication boundary.
    const port: TimelinePublicationPort = {
      preview: async (request) => previewEnvelope(request.draftRevision),
      apply: async () => { throw new TimelineConflictError() },
    }
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    act(() => result.current.editPeriods(result.current.state.draft.periods.map((period) => ({ ...period, co2_setpoint: 1100 }))))
    await act(async () => result.current.review())

    // When: Apply returns HTTP 409 through the typed conflict error.
    await act(async () => result.current.apply())

    // Then: the operator's values remain editable and the state is visibly conflicted.
    expect(result.current.state.draft.periods[0]?.co2_setpoint).toBe(1100)
    expect(result.current.state.status).toEqual({ kind: 'conflict' })
  })

  it('keeps the newest preview when an older request resolves last', async () => {
    // Given: two pending preview requests for successive draft revisions.
    let resolveFirst: ((value: ReturnType<typeof previewEnvelope>) => void) | undefined
    let resolveSecond: ((value: ReturnType<typeof previewEnvelope>) => void) | undefined
    let previewCount = 0
    const port: TimelinePublicationPort = {
      preview: () => new Promise((resolve) => {
        previewCount += 1
        if (previewCount === 1) resolveFirst = resolve
        if (previewCount === 2) resolveSecond = resolve
      }),
      apply: async () => savedBaseline(),
    }
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    let firstReview: Promise<void> | undefined
    let secondReview: Promise<void> | undefined

    // When: a newer review begins and resolves before the stale first preview.
    act(() => { firstReview = result.current.review() })
    act(() => result.current.editPeriods(result.current.state.draft.periods.map((period) => ({ ...period, heating_setpoint: 24 }))))
    act(() => { secondReview = result.current.review() })
    await act(async () => { resolveSecond?.(previewEnvelope(1)) })
    await act(async () => { resolveFirst?.(previewEnvelope(0)) })
    await firstReview
    await secondReview

    // Then: the draft-owned latest preview remains visible.
    expect(result.current.preview).toMatchObject({ kind: 'ready', draftRevision: 1 })
  })

  it('invalidates an in-flight preview when the table draft changes before another review', async () => {
    // Given: an in-flight preview for the current primary-table draft.
    let resolvePreview: ((value: ReturnType<typeof previewEnvelope>) => void) | undefined
    const port: TimelinePublicationPort = {
      preview: () => new Promise((resolve) => { resolvePreview = resolve }),
      apply: async () => savedBaseline(),
    }
    const { result } = renderHook(() => useTimelineDraft({ saved: savedBaseline(), publicationPort: port }))
    let review: Promise<void> | undefined
    act(() => { review = result.current.review() })

    // When: the table changes before that preview resolves.
    act(() => result.current.editPeriods(result.current.state.draft.periods.map((period) => ({ ...period, heating_setpoint: 24 }))))
    await act(async () => { resolvePreview?.(previewEnvelope(0)) })
    await review

    // Then: the stale response cannot repopulate preview state.
    expect(result.current.preview).toEqual({ kind: 'idle' })
  })
})
