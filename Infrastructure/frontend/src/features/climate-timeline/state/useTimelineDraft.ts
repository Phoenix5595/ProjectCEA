import { useCallback, useRef, useState } from 'react'

import { logger } from '../../../utils/logger'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TimelineSavedRequest } from '../api/timeline'
import {
  TimelineConflictError,
  TimelinePreviewIdentityError,
  type TimelineApplyOutcome,
  type TimelinePreviewRequest,
  type TimelinePreviewResult,
  type TimelinePublicationPort,
} from '../api/timelinePublicationPort'

import {
  applyTimelineWindowRead,
  createTimelineDraft,
  discardTimelineDraft,
  isTimelineDraftPersisted,
  markTimelineDraftConflict,
  reanchorTimelineSavedBaseline,
  requestTimelineRoomSwitch,
  reviewTimelineDraft,
  updateTimelineDraftPeriods,
  updateTimelineDraftPhotoperiod,
  type TimelineDraft,
  type TimelinePhotoperiod,
  type TimelineOwnedValues,
  type TimelineRoom,
  type TimelineSavedBaseline,
  type TimelineWindow,
} from './timelineDraft'

export type TimelinePreviewState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'loading'; readonly draftRevision: number }
  | {
      readonly kind: 'ready'
      readonly draftRevision: number
      readonly result: TimelinePreviewResult
      readonly request: TimelinePreviewRequest
      readonly source: 'preview'
    }
  | { readonly kind: 'failed'; readonly draftRevision: number }

export type TimelineSaveResult =
  | { readonly kind: 'saved'; readonly baseline: TimelineSavedBaseline; readonly warning: string | null }
  | { readonly kind: 'unchanged'; readonly baseline: TimelineSavedBaseline }
  | { readonly kind: 'conflict' }
  | { readonly kind: 'failed'; readonly error: unknown }
  | { readonly kind: 'superseded'; readonly committedBaseline?: TimelineSavedBaseline }
  | { readonly kind: 'busy' }

export type TimelineReviewResult =
  | {
      readonly kind: 'reviewed'
      readonly result: TimelinePreviewResult
      readonly request: TimelinePreviewRequest
    }
  | { readonly kind: 'conflict' }
  | { readonly kind: 'failed'; readonly error: unknown }
  | { readonly kind: 'superseded' }
  | { readonly kind: 'busy' }

export type TimelineApplyResult =
  | { readonly kind: 'applied'; readonly baseline: TimelineSavedBaseline; readonly warning: string | null }
  | { readonly kind: 'not-ready' }
  | { readonly kind: 'conflict' }
  | { readonly kind: 'failed'; readonly error: unknown }
  | { readonly kind: 'superseded'; readonly committedBaseline?: TimelineSavedBaseline }
  | { readonly kind: 'busy' }

export type TimelineRoomSwitchResult = 'switched' | 'requires-discard'

export type UseTimelineDraftOptions = {
  readonly saved: TimelineSavedBaseline
  readonly publicationPort: TimelinePublicationPort
  /**
   * Same-profile window re-read used by setWindow: exact saved authority for
   * the requested window, falling back to the configuration-only aggregate.
   */
  readonly loadWindowBaseline?: (request: TimelineSavedRequest) => Promise<TimelineSavedBaseline>
  readonly initializeDraftValues?: (baseline: TimelineSavedBaseline) => TimelineOwnedValues
}

export type TimelineDraftController = {
  readonly state: TimelineDraft
  readonly preview: TimelinePreviewState
  readonly saveWarning: string | null
  editPeriods(periods: readonly ClimatePeriod[]): void
  editPhotoperiod(photoperiod: TimelinePhotoperiod): void
  review(): Promise<TimelineReviewResult>
  apply(): Promise<TimelineApplyResult>
  save(): Promise<TimelineSaveResult>
  setWindow(window: TimelineWindow): void
  reconcileSaved(baseline: TimelineSavedBaseline): void
  discard(): void
  switchRoom(nextSaved: TimelineSavedBaseline): TimelineRoomSwitchResult
  confirmRoomSwitch(nextSaved: TimelineSavedBaseline): void
}

function requestFor(state: TimelineDraft, requestSeq: number): TimelinePreviewRequest {
  const { modeId, window, baseConfigRevision } = state.saved
  if (
    modeId == null || !Number.isSafeInteger(modeId) || modeId <= 0 ||
    !window || !baseConfigRevision
  ) throw new Error('Selected profile metadata is unavailable')
  return {
    room: state.saved.room,
    requestId: `timeline-${state.saved.room.location}-${state.draftRevision}-${requestSeq}`,
    expectedConfigRevision: baseConfigRevision,
    draftRevision: state.draftRevision,
    modeId,
    submodeId: state.saved.submodeId ?? null,
    window,
    values: state.draft,
  }
}

function sameWindow(
  left: TimelineWindow,
  right: { readonly start: string | Date; readonly end: string | Date; readonly timezone: string }
): boolean {
  const start = right.start instanceof Date ? right.start.getTime() : Date.parse(right.start)
  const end = right.end instanceof Date ? right.end.getTime() : Date.parse(right.end)
  return (
    Date.parse(left.start) === start &&
    Date.parse(left.end) === end &&
    left.timezone === right.timezone
  )
}

function previewMatchesRequest(
  result: TimelinePreviewResult,
  request: TimelinePreviewRequest
): boolean {
  return (
    result.requestId === request.requestId &&
    result.expectedConfigRevision === request.expectedConfigRevision &&
    result.draftRevision === request.draftRevision &&
    result.modeId === request.modeId &&
    result.submodeId === request.submodeId &&
    sameWindow(request.window, result.window)
  )
}

function requestMatchesState(request: TimelinePreviewRequest, state: TimelineDraft): boolean {
  return (
    request.room.location === state.saved.room.location &&
    request.room.cluster === state.saved.room.cluster &&
    request.expectedConfigRevision === state.saved.baseConfigRevision &&
    request.draftRevision === state.draftRevision &&
    request.modeId === (state.saved.modeId ?? 0) &&
    request.submodeId === (state.saved.submodeId ?? null) &&
    request.window.start === (state.saved.window ?? request.window).start &&
    request.window.end === (state.saved.window ?? request.window).end &&
    request.window.timezone === (state.saved.window ?? request.window).timezone
  )
}

function profileIdentityMatches(
  state: TimelineDraft,
  identity: {
    readonly room: TimelineRoom
    readonly modeId: number | null
    readonly submodeId: number | null
  }
): boolean {
  return (
    state.saved.room.location === identity.room.location &&
    state.saved.room.cluster === identity.room.cluster &&
    (state.saved.modeId ?? null) === identity.modeId &&
    (state.saved.submodeId ?? null) === identity.submodeId
  )
}

/**
 * Refuse to regress a known hexadecimal authority cursor. Room, profile and
 * request-generation fences handle authorities without an ordered cursor.
 */
function regressesSavedAuthority(
  latest: TimelineSavedBaseline,
  committed: TimelineSavedBaseline
): boolean {
  const latestRank = /^[0-9a-f]+$/i.test(latest.baseConfigRevision)
    ? Number.parseInt(latest.baseConfigRevision, 16)
    : null
  const committedRank = /^[0-9a-f]+$/i.test(committed.baseConfigRevision)
    ? Number.parseInt(committed.baseConfigRevision, 16)
    : null
  if (latestRank !== null && committedRank !== null) {
    return committedRank < latestRank
  }
  return false
}


export function useTimelineDraft({
  saved,
  publicationPort,
  loadWindowBaseline,
  initializeDraftValues,
}: UseTimelineDraftOptions): TimelineDraftController {
  const [state, setState] = useState(() => createTimelineDraft(saved, initializeDraftValues?.(saved)))
  const [preview, setPreview] = useState<TimelinePreviewState>({ kind: 'idle' })
  const [saveWarning, setSaveWarning] = useState<string | null>(null)
  const stateRef = useRef(state)
  const previewRef = useRef<TimelinePreviewState>(preview)
  const publicationPortRef = useRef(publicationPort)
  publicationPortRef.current = publicationPort
  const loadWindowBaselineRef = useRef(loadWindowBaseline)
  loadWindowBaselineRef.current = loadWindowBaseline
  const initializerRef = useRef(initializeDraftValues)
  initializerRef.current = initializeDraftValues
  const createDraft = useCallback(
    (baseline: TimelineSavedBaseline) => createTimelineDraft(baseline, initializerRef.current?.(baseline)),
    []
  )

  /** Bumped by every user-visible mutation; late command results compare it. */
  const fenceToken = useRef(0)
  /** Bumped at each review start so only the newest preview publishes. */
  const reviewSeq = useRef(0)
  /** Bumped at each setWindow so only the newest window read publishes. */
  const windowSeq = useRef(0)
  /** The pipeline command that owns review+apply sequencing; others see busy. */
  const commandRef = useRef<'save' | 'apply' | null>(null)

  const publishState = useCallback((next: TimelineDraft) => {
    stateRef.current = next
    setState(next)
  }, [])

  const publishPreview = useCallback((next: TimelinePreviewState) => {
    previewRef.current = next
    setPreview(next)
  }, [])

  const editPeriods = useCallback(
    (periods: readonly ClimatePeriod[]) => {
      fenceToken.current += 1
      publishState(updateTimelineDraftPeriods(stateRef.current, periods))
      publishPreview({ kind: 'idle' })
      setSaveWarning(null)
    },
    [publishPreview, publishState]
  )

  const editPhotoperiod = useCallback(
    (photoperiod: TimelinePhotoperiod) => {
      fenceToken.current += 1
      publishState(updateTimelineDraftPhotoperiod(stateRef.current, photoperiod))
      publishPreview({ kind: 'idle' })
      setSaveWarning(null)
    },
    [publishPreview, publishState]
  )

  const runReviewPhase = useCallback(
    async (
      request: TimelinePreviewRequest,
      token: number,
      seq: number
    ): Promise<TimelineReviewResult> => {
      publishState(reviewTimelineDraft(stateRef.current))
      publishPreview({ kind: 'loading', draftRevision: request.draftRevision })
      try {
        const result = await publicationPortRef.current.preview(request)
        if (fenceToken.current !== token || reviewSeq.current !== seq) {
          return { kind: 'superseded' }
        }
        if (!previewMatchesRequest(result, request)) {
          publishPreview({ kind: 'failed', draftRevision: request.draftRevision })
          return { kind: 'failed', error: new TimelinePreviewIdentityError(request.requestId) }
        }
        publishPreview({
          kind: 'ready',
          draftRevision: request.draftRevision,
          result,
          request,
          source: 'preview',
        })
        return { kind: 'reviewed', result, request }
      } catch (error) {
        if (fenceToken.current !== token || reviewSeq.current !== seq) {
          return { kind: 'superseded' }
        }
        if (error instanceof TimelineConflictError) {
          publishState(markTimelineDraftConflict(stateRef.current))
          publishPreview({ kind: 'idle' })
          return { kind: 'conflict' }
        }
        publishPreview({ kind: 'failed', draftRevision: request.draftRevision })
        return { kind: 'failed', error }
      }
    },
    [publishPreview, publishState]
  )

  const review = useCallback(async (): Promise<TimelineReviewResult> => {
    if (commandRef.current !== null) return { kind: 'busy' }
    const latest = stateRef.current
    if (latest.status.kind === 'conflict') return { kind: 'conflict' }
    if (
      latest.saved.modeId == null || !Number.isSafeInteger(latest.saved.modeId) ||
      latest.saved.modeId <= 0 || !latest.saved.window || !latest.saved.baseConfigRevision
    ) return { kind: 'failed', error: new Error('Selected profile metadata is unavailable') }
    const seq = reviewSeq.current + 1
    reviewSeq.current = seq
    const request = requestFor(latest, seq)
    return runReviewPhase(request, fenceToken.current, seq)
  }, [runReviewPhase])

  /**
   * A successful commit that the fence outran: the server kept the reviewed
   * values, but newer edits or a profile switch happened meanwhile. Re-anchor
   * saved authority for the same profile only; never claim the newer draft.
   */
  const resolveLateCommit = useCallback(
    (outcome: TimelineApplyOutcome, request: TimelinePreviewRequest): TimelineApplyResult => {
      const latest = stateRef.current
      if (
        profileIdentityMatches(latest, request) &&
        !regressesSavedAuthority(latest.saved, outcome.baseline)
      ) {
        // The re-anchor invalidates any in-flight preview built on the
        // superseded saved revision.
        reviewSeq.current += 1
        publishState(reanchorTimelineSavedBaseline(latest, outcome.baseline))
        publishPreview({ kind: 'idle' })
        setSaveWarning(outcome.warning)
      }
      return { kind: 'superseded', committedBaseline: outcome.baseline }
    },
    [publishPreview, publishState]
  )

  const commitApplyOutcome = useCallback(
    (outcome: TimelineApplyOutcome): TimelineApplyResult => {
      publishState(createDraft(outcome.baseline))
      publishPreview({ kind: 'idle' })
      setSaveWarning(outcome.warning)
      return { kind: 'applied', baseline: outcome.baseline, warning: outcome.warning }
    },
    [createDraft, publishPreview, publishState]
  )

  const handleApplyConflict = useCallback((): void => {
    // The conflict invalidates any in-flight preview built on the same stale
    // expected revision.
    reviewSeq.current += 1
    publishState(markTimelineDraftConflict(stateRef.current))
    publishPreview({ kind: 'idle' })
  }, [publishPreview, publishState])

  const apply = useCallback(async (): Promise<TimelineApplyResult> => {
    if (commandRef.current !== null) return { kind: 'busy' }
    const latest = stateRef.current
    const previewNow = previewRef.current
    if (
      latest.status.kind !== 'reviewed' ||
      latest.status.draftRevision !== latest.draftRevision ||
      previewNow.kind !== 'ready' ||
      previewNow.draftRevision !== latest.draftRevision ||
      !requestMatchesState(previewNow.request, latest)
    ) {
      return { kind: 'not-ready' }
    }
    commandRef.current = 'apply'
    const request = previewNow.request
    const token = fenceToken.current
    try {
      const outcome = await publicationPortRef.current.apply(request)
      if (fenceToken.current !== token) return resolveLateCommit(outcome, request)
      return commitApplyOutcome(outcome)
    } catch (error) {
      if (fenceToken.current !== token) return { kind: 'superseded' }
      if (error instanceof TimelineConflictError) {
        handleApplyConflict()
        return { kind: 'conflict' }
      }
      return { kind: 'failed', error }
    } finally {
      commandRef.current = null
    }
  }, [commitApplyOutcome, handleApplyConflict, publishPreview, publishState, resolveLateCommit])

  const save = useCallback(async (): Promise<TimelineSaveResult> => {
    if (commandRef.current !== null) return { kind: 'busy' }
    const latest = stateRef.current
    if (latest.status.kind === 'conflict') return { kind: 'conflict' }
    if (
      latest.saved.modeId == null || !Number.isSafeInteger(latest.saved.modeId) ||
      latest.saved.modeId <= 0 || !latest.saved.window || !latest.saved.baseConfigRevision
    ) return { kind: 'failed', error: new Error('Selected profile metadata is unavailable') }
    // Persisted matching values (including a reverted edit) need no commit;
    // unconfigured parameters and a locally initialized constant row do.
    if (isTimelineDraftPersisted(latest)) {
      return { kind: 'unchanged', baseline: latest.saved }
    }
    commandRef.current = 'save'
    const seq = reviewSeq.current + 1
    reviewSeq.current = seq
    const request = requestFor(latest, seq)
    const token = fenceToken.current
    try {
      const reviewed = await runReviewPhase(request, token, seq)
      if (reviewed.kind !== 'reviewed') {
        if (reviewed.kind === 'failed') return { kind: 'failed', error: reviewed.error }
        if (reviewed.kind === 'conflict') return { kind: 'conflict' }
        return { kind: 'superseded' }
      }
      const outcome = await publicationPortRef.current.apply(request)
      if (fenceToken.current !== token) {
        const late = resolveLateCommit(outcome, request)
        return late.kind === 'superseded'
          ? { kind: 'superseded', committedBaseline: late.committedBaseline }
          : { kind: 'superseded' }
      }
      const applied = commitApplyOutcome(outcome)
      if (applied.kind === 'applied') {
        return { kind: 'saved', baseline: applied.baseline, warning: applied.warning }
      }
      if (applied.kind === 'superseded') return applied
      if (applied.kind === 'failed') return { kind: 'failed', error: applied.error }
      return { kind: 'superseded' }
    } catch (error) {
      if (fenceToken.current !== token) return { kind: 'superseded' }
      if (error instanceof TimelineConflictError) {
        handleApplyConflict()
        return { kind: 'conflict' }
      }
      return { kind: 'failed', error }
    } finally {
      commandRef.current = null
    }
  }, [commitApplyOutcome, handleApplyConflict, publishPreview, publishState, resolveLateCommit, runReviewPhase])

  const setWindow = useCallback(
    (window: TimelineWindow): void => {
      const latest = stateRef.current
      if (latest.saved.window && sameWindow(latest.saved.window, window)) return
      fenceToken.current += 1
      windowSeq.current += 1
      const seq = windowSeq.current
      const room = latest.saved.room
      const modeId = latest.saved.modeId
      const submodeId = latest.saved.submodeId ?? null
      // Adopt the operator's window immediately: a pending read must not leave
      // the previous window's envelope stretched across the new display.
      publishState({
        ...latest,
        saved: { ...latest.saved, window, trajectory: undefined },
        status: latest.status.kind === 'reviewed' ? { kind: 'editing' } : latest.status,
      })
      publishPreview({ kind: 'idle' })
      const loader = loadWindowBaselineRef.current
      if (loader == null || modeId == null) return
      void loader({ location: room.location, cluster: room.cluster, modeId, submodeId, window })
        .then(baseline => {
          if (windowSeq.current !== seq) return
          const current = stateRef.current
          if (!profileIdentityMatches(current, { room, modeId, submodeId })) return
          if (regressesSavedAuthority(current.saved, baseline)) return
          publishState(applyTimelineWindowRead(current, baseline, initializerRef.current?.(baseline)).state)
        })
        .catch(error => {
          if (windowSeq.current !== seq) return
          // Values and the expected revision stay; the chart degrades to the
          // local estimate until a matching window read succeeds.
          logger.error('Failed to load timeline window:', error)
        })
    },
    [publishPreview, publishState]
  )

  const reconcileSaved = useCallback((baseline: TimelineSavedBaseline) => {
    const latest = stateRef.current
    if (!profileIdentityMatches(latest, {
      room: baseline.room, modeId: baseline.modeId ?? null, submodeId: baseline.submodeId ?? null,
    })) return
    if (latest.saved.window && baseline.window && !sameWindow(latest.saved.window, baseline.window)) return
    if (regressesSavedAuthority(latest.saved, baseline)) return
    const authorityChanged = baseline.baseConfigRevision !== latest.saved.baseConfigRevision
    const result = applyTimelineWindowRead(latest, baseline, initializerRef.current?.(baseline))
    if (authorityChanged) {
      fenceToken.current += 1
      reviewSeq.current += 1
      publishPreview({ kind: 'idle' })
    }
    publishState(result.state)
  }, [publishPreview, publishState])

  const discard = useCallback(() => {
    fenceToken.current += 1
    publishState(discardTimelineDraft(stateRef.current))
    publishPreview({ kind: 'idle' })
    setSaveWarning(null)
  }, [publishPreview, publishState])

  const switchRoom = useCallback(
    (nextSaved: TimelineSavedBaseline): TimelineRoomSwitchResult => {
      const result = requestTimelineRoomSwitch(stateRef.current, nextSaved)
      if (result.kind === 'requires-discard') return result.kind
      fenceToken.current += 1
      windowSeq.current += 1
      reviewSeq.current += 1
      publishState(createDraft(nextSaved))
      publishPreview({ kind: 'idle' })
      setSaveWarning(null)
      return result.kind
    },
    [createDraft, publishPreview, publishState]
  )

  const confirmRoomSwitch = useCallback(
    (nextSaved: TimelineSavedBaseline) => {
      fenceToken.current += 1
      windowSeq.current += 1
      reviewSeq.current += 1
      publishState(createDraft(nextSaved))
      publishPreview({ kind: 'idle' })
      setSaveWarning(null)
    },
    [createDraft, publishPreview, publishState]
  )

  return {
    state,
    preview,
    saveWarning,
    editPeriods,
    editPhotoperiod,
    review,
    apply,
    save,
    setWindow,
    discard,
    reconcileSaved,
    switchRoom,
    confirmRoomSwitch,
  }
}
