import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { createClimatePeriodsTableAdapter } from '../adapters/climatePeriodsTableAdapter'
import { RichTrajectoryEnvelope } from '../api/contracts'
import {
  TimelineConflictError,
  type TimelineApplyOutcome,
  type TimelineApplyRequest,
  type TimelinePreviewRequest,
  type TimelinePreviewResult,
  type TimelinePublicationPort,
} from '../api/timelinePublicationPort'
import type {
  TimelineApplyResult,
  TimelineReviewResult,
  TimelineSaveResult,
} from '../state/useTimelineDraft'
import type { TimelineSavedRequest } from '../api/timeline'
import {
  isTimelineDraftDirty,
  type TimelineSavedBaseline,
} from '../state/timelineDraft'
import { useTimelineDraft } from '../state/useTimelineDraft'

const savedBaseline = (revision = 'config-1'): TimelineSavedBaseline => ({
  room: { location: 'flower', cluster: 'main' },
  baseConfigRevision: revision,
  modeId: 17,
  submodeId: 3,
  parametersConfigured: true,
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    timezone: 'America/Toronto',
  },
  periods: [
    {
      period_name: 'Day',
      start_time: '06:00',
      end_time: '18:00',
      ramp_minutes: 30,
      heating_setpoint: 22,
      cooling_setpoint: 25,
      vpd_setpoint: 1.2,
      co2_setpoint: 900,
      details: 'saved',
    },
  ],
  photoperiod: {
    dayStartTime: '06:00',
    nightStartTime: '18:00',
    rampUpMinutes: 20,
    rampDownMinutes: 20,
  },
  trajectory: previewEnvelope(0),
})

function savedEnvelopeFixture() {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Flower Room',
    generated_at: '2026-01-01T00:00:00.000Z',
    window: {
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-02T00:00:00.000Z',
      timezone: 'America/Toronto',
    },
    revision_scope: 'saved',
    base_config_revision: 'config-2',
    draft_revision: null,
    segments: [
      {
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
          config_revision: 'config-2',
          draft_revision: null,
        },
      },
    ],
    assumptions: [],
    warnings: [],
  })
}

function previewEnvelope(draftRevision: number) {
  return RichTrajectoryEnvelope.parse({
    contract_version: 1,
    room: 'Flower Room',
    generated_at: '2026-01-01T00:00:00.000Z',
    window: {
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-02T00:00:00.000Z',
      timezone: 'America/Toronto',
    },
    revision_scope: 'draft',
    base_config_revision: 'config-1',
    draft_revision: `draft-${draftRevision}`,
    segments: [
      {
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
      },
    ],
    assumptions: [],
    warnings: [],
  })
}
function previewResult(
  request: TimelinePreviewRequest,
  trajectory = previewEnvelope(request.draftRevision)
): TimelinePreviewResult {
  return {
    requestId: request.requestId,
    expectedConfigRevision: request.expectedConfigRevision,
    draftRevision: request.draftRevision,
    modeId: request.modeId,
    submodeId: request.submodeId,
    window: request.window,
    trajectory,
  }
}

function applyOutcome(baseline: TimelineSavedBaseline): TimelineApplyOutcome {
  return { baseline, warning: null }
}

function successfulPort(): TimelinePublicationPort {
  return {
    preview: async request => previewResult(request),
    apply: async request =>
      applyOutcome({
        room: request.room,
        baseConfigRevision: 'config-2',
        modeId: request.modeId,
        submodeId: request.submodeId,
        parametersConfigured: true,
        ...request.values,
      }),
  }
}

describe('useTimelineDraft', () => {

  it('reviews and applies a primary-table-only edit without applying light settings', async () => {
    // Given: a shared draft exposed to the existing controlled primary table.
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: successfulPort() })
    )
    const table = createClimatePeriodsTableAdapter(result.current.state, result.current.editPeriods)

    // When: the table changes climate periods, then the operator reviews and applies the draft.
    act(() => table.onChange(table.periods.map(period => ({ ...period, heating_setpoint: 23.5 }))))
    await act(async () => result.current.review())
    await act(async () => result.current.apply())

    // Then: the saved timeline baseline advances and its aggregate contains only timeline-owned values.
    expect(result.current.state.saved.periods[0]?.heating_setpoint).toBe(23.5)
    expect(result.current.state.saved.baseConfigRevision).toBe('config-2')
    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })

  it('reflects draft edits back into the table adapter view', () => {
    // Given: a shared draft controller and its table adapter.
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: successfulPort() })
    )
    const before = createClimatePeriodsTableAdapter(
      result.current.state,
      result.current.editPeriods
    )

    // When: the draft periods change from the timeline side.
    act(() =>
      result.current.editPeriods(
        before.periods.map(period => ({ ...period, cooling_setpoint: 26 }))
      )
    )
    const after = createClimatePeriodsTableAdapter(result.current.state, result.current.editPeriods)

    // Then: the adapter view carries the edited value for the table row.
    expect(after.periods[0]?.cooling_setpoint).toBe(26)
  })

  it('warns before a dirty room switch and keeps the original draft until confirmed', () => {
    // Given: an edited Flower draft.
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: successfulPort() })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, details: 'unsaved' }))
      )
    )
    const vegetation = { ...savedBaseline(), room: { location: 'vegetation', cluster: 'main' } }

    // When: navigation requests another room before the operator discards the draft.
    let switchResult: string = ''
    act(() => {
      switchResult = result.current.switchRoom(vegetation)
    })

    // Then: the original dirty draft remains in place until explicit confirmation.
    expect(switchResult).toBe('requires-discard')
    expect(result.current.state.saved.room.location).toBe('flower')
    expect(result.current.state.draft.periods[0]?.details).toBe('unsaved')
  })

  it('preserves the reviewed draft after Apply receives a revision conflict', async () => {
    // Given: a reviewed timeline draft and a revision-checked publication boundary.
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: async () => {
        throw new TimelineConflictError()
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, co2_setpoint: 1100 }))
      )
    )
    await act(async () => result.current.review())

    // When: Apply returns HTTP 409 through the typed conflict error.
    await act(async () => result.current.apply())

    // Then: the operator's values remain editable and the state is visibly conflicted.
    expect(result.current.state.draft.periods[0]?.co2_setpoint).toBe(1100)
    expect(result.current.state.status).toEqual({ kind: 'conflict' })
  })

  it('keeps the newest preview when an older request resolves last', async () => {
    // Given: two pending preview requests for successive draft revisions.
    let resolveFirst: (() => void) | undefined
    let resolveSecond: (() => void) | undefined
    let previewCount = 0
    const port: TimelinePublicationPort = {
      preview: request => {
        const { promise, resolve } = Promise.withResolvers<TimelinePreviewResult>()
        previewCount += 1
        if (previewCount === 1) {
          resolveFirst = () => resolve(previewResult(request))
        }
        if (previewCount === 2) {
          resolveSecond = () => resolve(previewResult(request))
        }
        return promise
      },
      apply: async () => applyOutcome(savedBaseline()),
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    let firstReview: Promise<unknown> | undefined
    let secondReview: Promise<unknown> | undefined

    // When: a newer review begins and resolves before the stale first preview.
    act(() => {
      firstReview = result.current.review()
    })
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 24 }))
      )
    )
    act(() => {
      secondReview = result.current.review()
    })
    await act(async () => {
      resolveSecond?.()
    })
    await act(async () => {
      resolveFirst?.()
    })
    await firstReview
    await secondReview

    // Then: the draft-owned latest preview remains visible.
    expect(result.current.preview).toMatchObject({ kind: 'ready', draftRevision: 1 })
  })

  it('invalidates an in-flight preview when the table draft changes before another review', async () => {
    // Given: an in-flight preview for the current primary-table draft.
    let resolvePreview: (() => void) | undefined
    const port: TimelinePublicationPort = {
      preview: request => {
        const { promise, resolve } = Promise.withResolvers<TimelinePreviewResult>()
        resolvePreview = () => resolve(previewResult(request))
        return promise
      },
      apply: async () => applyOutcome(savedBaseline()),
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    let review: Promise<unknown> | undefined
    act(() => {
      review = result.current.review()
    })

    // When: the table changes before that preview resolves.
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 24 }))
      )
    )
    await act(async () => {
      resolvePreview?.()
    })
    await review

    // Then: the stale response cannot repopulate preview state.
    expect(result.current.preview).toEqual({ kind: 'idle' })
  })

  it('does not Apply while the reviewed preview is loading or failed', async () => {
    let resolvePreview: (() => void) | undefined
    let applyCalls = 0
    let shouldFail = false
    const port: TimelinePublicationPort = {
      preview: request => {
        if (shouldFail) return Promise.reject(new Error('preview failed'))
        const { promise, resolve } = Promise.withResolvers<TimelinePreviewResult>()
        resolvePreview = () => resolve(previewResult(request))
        return promise
      },
      apply: async () => {
        applyCalls += 1
        return applyOutcome(savedBaseline())
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    let review: Promise<unknown> | undefined
    act(() => {
      review = result.current.review()
    })
    await act(async () => result.current.apply())
    expect(applyCalls).toBe(0)

    await act(async () => {
      resolvePreview?.()
    })
    await review
    expect(result.current.preview.kind).toBe('ready')
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, details: 'failed review' }))
      )
    )
    shouldFail = true
    await act(async () => result.current.review())
    await act(async () => result.current.apply())

    expect(result.current.preview.kind).toBe('failed')
    expect(applyCalls).toBe(0)
    shouldFail = false
    const originalPreview = port.preview
    port.preview = async request => ({
      ...previewResult(request),
      expectedConfigRevision: 'wrong',
    })
    await act(async () => result.current.review())
    await act(async () => result.current.apply())
    expect(result.current.preview.kind).toBe('failed')
    expect(applyCalls).toBe(0)
    port.preview = originalPreview
  })

  it('commits only once while Apply is in flight', async () => {
    let previewRequestId = ''
    let applyRequestId = ''
    let resolveApply: ((value: TimelineApplyOutcome) => void) | undefined
    let applyCalls = 0
    const port: TimelinePublicationPort = {
      preview: async request => {
        previewRequestId = request.requestId
        return previewResult(request)
      },
      apply: request => {
        applyCalls += 1
        applyRequestId = request.requestId
        const { promise, resolve } = Promise.withResolvers<TimelineApplyOutcome>()
        resolveApply = resolve
        return promise
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23 }))
      )
    )
    await act(async () => result.current.review())
    let firstApply: Promise<unknown> | undefined
    let secondApply: Promise<unknown> | undefined
    act(() => {
      firstApply = result.current.apply()
    })
    act(() => {
      secondApply = result.current.apply()
    })
    expect(applyCalls).toBe(1)
    expect(applyRequestId).toBe(previewRequestId)
    await act(async () => {
      resolveApply?.(applyOutcome(savedBaseline('config-2')))
    })
    await firstApply
    await secondApply

    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })
})


describe('useTimelineDraft typed save results', () => {
  it('returns unchanged without any preview or apply traffic for a clean configured draft', async () => {
    let previewCalls = 0
    let applyCalls = 0
    const port: TimelinePublicationPort = {
      preview: async request => {
        previewCalls += 1
        return previewResult(request)
      },
      apply: async request => {
        applyCalls += 1
        return applyOutcome({ ...savedBaseline(), ...request.values })
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toMatchObject({ kind: 'unchanged' })
    expect(previewCalls).toBe(0)
    expect(applyCalls).toBe(0)
  })

  it('treats a reverted edit as persisted matching values, so a clean revert stays clean', async () => {
    let trafficCalls = 0
    const port: TimelinePublicationPort = {
      preview: async request => {
        trafficCalls += 1
        return previewResult(request)
      },
      apply: async request => {
        trafficCalls += 1
        return applyOutcome({ ...savedBaseline(), ...request.values })
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23.5 }))
      )
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 22 }))
      )
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(result.current.state.status.kind).toBe('editing')
    expect(isTimelineDraftDirty(result.current.state)).toBe(false)
    expect(saveResult).toMatchObject({ kind: 'unchanged' })
    expect(trafficCalls).toBe(0)
  })

  it('still saves unconfigured default values even when the draft is clean', async () => {
    let applyRequests: TimelineApplyRequest[] = []
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: async request => {
        applyRequests = [...applyRequests, request]
        return applyOutcome({
          room: request.room,
          baseConfigRevision: '0000002',
          modeId: request.modeId,
          submodeId: request.submodeId,
          parametersConfigured: true,
          ...request.values,
        })
      },
    }
    const unconfigured = { ...savedBaseline(), parametersConfigured: false }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: unconfigured, publicationPort: port })
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toMatchObject({ kind: 'saved', warning: null })
    expect(applyRequests[0]?.draftRevision).toBe(0)
    expect(result.current.state.saved.baseConfigRevision).toBe('0000002')
    expect(result.current.state.saved.parametersConfigured).toBe(true)
  })

  it('reviews then applies the captured dirty request without rerender timers', async () => {
    const appliedRequests: TimelineApplyRequest[] = []
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: async request => {
        appliedRequests.push(request)
        return applyOutcome({
          room: request.room,
          baseConfigRevision: '0000002',
          modeId: request.modeId,
          submodeId: request.submodeId,
          parametersConfigured: true,
          ...request.values,
        })
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23.5 }))
      )
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toMatchObject({ kind: 'saved', warning: null })
    expect(appliedRequests).toHaveLength(1)
    expect(appliedRequests[0]?.values.periods[0]?.heating_setpoint).toBe(23.5)
    expect(appliedRequests[0]?.draftRevision).toBe(1)
    expect(result.current.state.saved.baseConfigRevision).toBe('0000002')
    expect(result.current.state.saved.periods[0]?.heating_setpoint).toBe(23.5)
    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })

  it('returns conflict and preserves the draft when the save hits a revision conflict', async () => {
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: async () => {
        throw new TimelineConflictError()
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, co2_setpoint: 1100 }))
      )
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toMatchObject({ kind: 'conflict' })
    expect(result.current.state.draft.periods[0]?.co2_setpoint).toBe(1100)
    expect(result.current.state.status).toEqual({ kind: 'conflict' })
  })

  it('fails with the identity error for a baseline without a selected profile', async () => {
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: async request => applyOutcome({ ...savedBaseline(), ...request.values }),
    }
    const { result } = renderHook(() =>
      useTimelineDraft({
        saved: {
          room: { location: '', cluster: '' },
          baseConfigRevision: '',
          periods: [],
          photoperiod: { dayStartTime: '00:00', nightStartTime: '00:00', rampUpMinutes: 0, rampDownMinutes: 0 },
          parametersConfigured: false,
        },
        publicationPort: port,
      })
    )

    let saveResult: TimelineSaveResult | undefined
    await act(async () => {
      saveResult = await result.current.save()
    })

    expect(saveResult).toMatchObject({ kind: 'failed' })
  })

  it('returns busy for a concurrent save and defers review while the save pipeline runs', async () => {
    let releasePreview: (() => void) | undefined
    let saveCalls = 0
    const port: TimelinePublicationPort = {
      preview: request => {
        const { promise, resolve } = Promise.withResolvers<TimelinePreviewResult>()
        releasePreview = () => resolve(previewResult(request))
        return promise
      },
      apply: async request => {
        saveCalls += 1
        return applyOutcome({
          room: request.room,
          baseConfigRevision: '0000002',
          modeId: request.modeId,
          submodeId: request.submodeId,
          parametersConfigured: true,
          ...request.values,
        })
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23 }))
      )
    )
    let firstSave: Promise<TimelineSaveResult> | undefined
    act(() => {
      firstSave = result.current.save()
    })

    let concurrentSave: TimelineSaveResult | undefined
    let concurrentReview: TimelineReviewResult | undefined
    await act(async () => {
      concurrentSave = await result.current.save()
      concurrentReview = await result.current.review()
    })

    expect(concurrentSave).toMatchObject({ kind: 'busy' })
    expect(concurrentReview).toMatchObject({ kind: 'busy' })
    await act(async () => {
      releasePreview?.()
    })
    const settled = await firstSave
    expect(settled).toMatchObject({ kind: 'saved' })
    expect(saveCalls).toBe(1)
  })
})

describe('useTimelineDraft typed review results', () => {
  it('returns the reviewed result with its exact request for a NULL-target draft', async () => {
    let reviewOutcome: TimelineReviewResult | undefined
    const port: TimelinePublicationPort = {
      preview: async request => ({ ...previewResult(request), trajectory: null }),
      apply: async request =>
        applyOutcome({
          room: request.room,
          baseConfigRevision: '0000002',
          modeId: request.modeId,
          submodeId: request.submodeId,
          parametersConfigured: true,
          ...request.values,
        }),
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, details: 'all-null draft' }))
      )
    )

    await act(async () => {
      reviewOutcome = await result.current.review()
    })

    expect(reviewOutcome?.kind).toBe('reviewed')
    if (reviewOutcome?.kind !== 'reviewed') return
    expect(reviewOutcome.result.trajectory).toBeNull()
    expect(reviewOutcome.request.draftRevision).toBe(1)
    expect(result.current.preview).toMatchObject({ kind: 'ready', draftRevision: 1 })

    // A NULL review stays eligible for Apply with no numeric curve.
    let applyResult: TimelineApplyResult | undefined
    await act(async () => {
      applyResult = await result.current.apply()
    })
    expect(applyResult).toMatchObject({ kind: 'applied' })
    expect(result.current.state.saved.trajectory).toBeUndefined()
    expect(result.current.state.saved.baseConfigRevision).toBe('0000002')
  })

  it('marks the conflict and keeps the draft when a review hits a stale expected revision', async () => {
    let reviewOutcome: TimelineReviewResult | undefined
    const port: TimelinePublicationPort = {
      preview: async () => {
        throw new TimelineConflictError('preview-1')
      },
      apply: async () => applyOutcome(savedBaseline()),
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved: savedBaseline(), publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 24 }))
      )
    )

    await act(async () => {
      reviewOutcome = await result.current.review()
    })

    expect(reviewOutcome).toMatchObject({ kind: 'conflict' })
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(24)
    expect(result.current.state.status).toEqual({ kind: 'conflict' })
    expect(result.current.preview).toEqual({ kind: 'idle' })
  })
})

describe('useTimelineDraft late commit fencing', () => {
  it('re-anchors the committed authority and keeps the newer edit dirty as superseded', async () => {
    const saved = savedBaseline('0000001')
    let resolveApply: ((value: TimelineApplyOutcome) => void) | undefined
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: () => {
        const { promise, resolve } = Promise.withResolvers<TimelineApplyOutcome>()
        resolveApply = resolve
        return promise
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved, publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23 }))
      )
    )
    await act(async () => result.current.review())
    let applyPromise: Promise<TimelineApplyResult> | undefined
    act(() => {
      applyPromise = result.current.apply()
    })
    // The operator keeps editing while the commit is in flight.
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 26 }))
      )
    )
    const committed = {
      ...saved,
      baseConfigRevision: '0000002',
      periods: saved.periods.map(period => ({ ...period, heating_setpoint: 23 })),
    }
    await act(async () => {
      resolveApply?.(applyOutcome(committed))
    })
    const applyResult = await applyPromise

    expect(applyResult).toMatchObject({ kind: 'superseded', committedBaseline: committed })
    expect(result.current.state.saved.baseConfigRevision).toBe('0000002')
    expect(result.current.state.saved.periods[0]?.heating_setpoint).toBe(23)
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(26)
    expect(isTimelineDraftDirty(result.current.state)).toBe(true)
  })

  it('never repoints another profile after a switch during the commit', async () => {
    const saved = savedBaseline('0000001')
    let resolveApply: ((value: TimelineApplyOutcome) => void) | undefined
    const port: TimelinePublicationPort = {
      preview: async request => previewResult(request),
      apply: () => {
        const { promise, resolve } = Promise.withResolvers<TimelineApplyOutcome>()
        resolveApply = resolve
        return promise
      },
    }
    const { result } = renderHook(() =>
      useTimelineDraft({ saved, publicationPort: port })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23 }))
      )
    )
    await act(async () => result.current.review())
    let applyPromise: Promise<TimelineApplyResult> | undefined
    act(() => {
      applyPromise = result.current.apply()
    })
    const vegetation = { ...savedBaseline('0000009'), room: { location: 'vegetation', cluster: 'main' } }
    act(() => result.current.confirmRoomSwitch(vegetation))
    await act(async () => {
      resolveApply?.(applyOutcome({ ...saved, baseConfigRevision: '0000002' }))
    })
    const applyResult = await applyPromise

    expect(applyResult).toMatchObject({ kind: 'superseded' })
    expect(result.current.state.saved.room.location).toBe('vegetation')
    expect(result.current.state.saved.baseConfigRevision).toBe('0000009')
    expect(isTimelineDraftDirty(result.current.state)).toBe(false)
  })
})

describe('useTimelineDraft setWindow', () => {
  const nextWindow = {
    start: '2026-01-05T00:00:00.000Z',
    end: '2026-01-06T00:00:00.000Z',
    timezone: 'UTC',
  }

  it('invalidates review, keeps the draft, and restores trajectory metadata from the window read', async () => {
    let loaderRequest: TimelineSavedRequest | undefined
    const loadWindowBaseline = vi.fn(async (request: TimelineSavedRequest) => {
      loaderRequest = request
      return { ...savedBaseline(), window: request.window, trajectory: savedEnvelopeFixture() }
    })
    const { result } = renderHook(() =>
      useTimelineDraft({
        saved: savedBaseline(),
        publicationPort: successfulPort(),
        loadWindowBaseline,
      })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 23 }))
      )
    )
    await act(async () => result.current.review())
    expect(result.current.state.status).toEqual({ kind: 'reviewed', draftRevision: 1 })

    act(() => result.current.setWindow(nextWindow))

    // The operator's window is adopted synchronously; the old-window envelope
    // never stretches into it and the review is invalidated.
    expect(result.current.state.saved.window).toEqual(nextWindow)
    expect(result.current.state.saved.trajectory).toBeUndefined()
    expect(result.current.state.status).toEqual({ kind: 'editing' })
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(23)
    expect(result.current.preview).toEqual({ kind: 'idle' })
    await waitFor(() => expect(loaderRequest?.window).toEqual(nextWindow))
    expect(loaderRequest?.modeId).toBe(17)
    expect(loaderRequest?.submodeId).toBe(3)

    // The matching read lands: trajectory metadata is restored for the new window.
    await waitFor(() =>
      expect(result.current.state.saved.trajectory).toEqual(savedEnvelopeFixture())
    )
    expect(result.current.state.saved.baseConfigRevision).toBe('config-1')
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(23)
  })

  it('replaces the whole baseline when the window read brings a newer authority to a clean draft', async () => {
    const loadWindowBaseline = vi.fn(async (request: TimelineSavedRequest) => ({
      ...savedBaseline('0000002'),
      window: request.window,
    }))
    const { result } = renderHook(() =>
      useTimelineDraft({
        saved: savedBaseline(),
        publicationPort: successfulPort(),
        loadWindowBaseline,
      })
    )

    act(() => result.current.setWindow(nextWindow))
    await waitFor(() => expect(result.current.state.saved.baseConfigRevision).toBe('0000002'))
    expect(result.current.state.saved.window).toEqual(nextWindow)
    expect(result.current.state.draft.periods).toEqual(result.current.state.saved.periods)
    expect(result.current.state.status).toEqual({ kind: 'editing' })
  })

  it('marks a conflict for a dirty draft instead of rebasing, keeping values and revision', async () => {
    const loadWindowBaseline = vi.fn(async (request: TimelineSavedRequest) => ({
      ...savedBaseline('0000002'),
      window: request.window,
    }))
    const { result } = renderHook(() =>
      useTimelineDraft({
        saved: savedBaseline(),
        publicationPort: successfulPort(),
        loadWindowBaseline,
      })
    )
    act(() =>
      result.current.editPeriods(
        result.current.state.draft.periods.map(period => ({ ...period, heating_setpoint: 24 }))
      )
    )

    act(() => result.current.setWindow(nextWindow))
    await waitFor(() => expect(result.current.state.status.kind).toBe('conflict'))
    expect(result.current.state.saved.baseConfigRevision).toBe('config-1')
    expect(result.current.state.draft.periods[0]?.heating_setpoint).toBe(24)
    expect(result.current.state.saved.window).toEqual(nextWindow)
  })

  it('ignores a superseded window read after another window change', async () => {
    let firstReadCalls = 0
    const loadWindowBaseline = vi.fn(async (request: TimelineSavedRequest) => {
      firstReadCalls += 1
      return { ...savedBaseline('0000002'), window: request.window }
    })
    const { result } = renderHook(() =>
      useTimelineDraft({
        saved: savedBaseline(),
        publicationPort: successfulPort(),
        loadWindowBaseline,
      })
    )
    const rollingWindow = {
      start: '2026-01-04T12:00:00.000Z',
      end: '2026-01-05T12:00:00.000Z',
      timezone: 'UTC',
    }
    act(() => result.current.setWindow(nextWindow))
    await waitFor(() => expect(firstReadCalls).toBe(1))
    act(() => result.current.setWindow(rollingWindow))
    await waitFor(() => expect(firstReadCalls).toBe(2))

    expect(result.current.state.saved.window).toEqual(rollingWindow)
    expect(result.current.state.saved.baseConfigRevision).toBe('0000002')
  })
})
