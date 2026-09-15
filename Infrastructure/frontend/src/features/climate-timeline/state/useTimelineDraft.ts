import { useCallback, useRef, useState } from 'react'
import type { RichTrajectoryEnvelope } from '../api/contracts'
import {
  TimelineConflictError,
  type TimelinePreviewRequest,
  type TimelinePublicationPort,
} from '../api/timelinePublicationPort'
import {
  applyTimelineDraft,
  createTimelineDraft,
  discardTimelineDraft,
  markTimelineDraftConflict,
  requestTimelineRoomSwitch,
  reviewTimelineDraft,
  updateTimelineDraftPeriods,
  updateTimelineDraftPhotoperiod,
  type TimelineDraft,
  type TimelinePhotoperiod,
  type TimelineSavedBaseline,
  type TimelineWindow,
} from './timelineDraft'
import type { ClimatePeriod } from '../../../types/climatePeriod'

export type TimelinePreviewState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'loading'; readonly draftRevision: number }
  | {
      readonly kind: 'ready'
      readonly draftRevision: number
      readonly value: RichTrajectoryEnvelope
      readonly request: TimelinePreviewRequest
      readonly source: 'preview' | 'saved'
    }
  | { readonly kind: 'failed'; readonly draftRevision: number }

export type TimelineRoomSwitchResult = 'switched' | 'requires-discard'

export type UseTimelineDraftOptions = {
  readonly saved: TimelineSavedBaseline
  readonly publicationPort: TimelinePublicationPort
}

export type TimelineDraftController = {
  readonly state: TimelineDraft
  readonly preview: TimelinePreviewState
  editPeriods(periods: readonly ClimatePeriod[]): void
  editPhotoperiod(photoperiod: TimelinePhotoperiod): void
  review(): Promise<void>
  apply(): Promise<void>
  discard(): void
  switchRoom(nextSaved: TimelineSavedBaseline): TimelineRoomSwitchResult
  confirmRoomSwitch(nextSaved: TimelineSavedBaseline): void
}

function defaultWindow(): TimelineWindow {
  const start = new Date()
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000)
  return { start: start.toISOString(), end: end.toISOString(), timezone: 'UTC' }
}

function requestFor(state: TimelineDraft): TimelinePreviewRequest {
  return {
    room: state.saved.room,
    requestId: `timeline-${state.saved.room.location}-${state.draftRevision}-${Date.now()}`,
    expectedConfigRevision: state.saved.baseConfigRevision,
    draftRevision: state.draftRevision,
    modeId: state.saved.modeId ?? 0,
    submodeId: state.saved.submodeId ?? null,
    window: state.saved.window ?? defaultWindow(),
    values: state.draft,
  }
}

function sameWindow(left: TimelineWindow, right: { readonly start: Date; readonly end: Date; readonly timezone: string }): boolean {
  return left.start === right.start.toISOString() && left.end === right.end.toISOString() && left.timezone === right.timezone
}

function previewMatchesRequest(value: RichTrajectoryEnvelope, request: TimelinePreviewRequest): boolean {
  const requestedRoom = request.room.location.toLowerCase()
  return value.revision_scope === 'draft'
    && value.room.toLowerCase().includes(requestedRoom === 'veg' ? 'vegetation' : requestedRoom)
    && value.base_config_revision === request.expectedConfigRevision
    && (value.draft_revision === String(request.draftRevision) || value.draft_revision === `draft-${request.draftRevision}`)
    && sameWindow(request.window, value.window)
}

function requestMatchesState(request: TimelinePreviewRequest, state: TimelineDraft): boolean {
  return request.room.location === state.saved.room.location
    && request.room.cluster === state.saved.room.cluster
    && request.expectedConfigRevision === state.saved.baseConfigRevision
    && request.draftRevision === state.draftRevision
    && request.modeId === (state.saved.modeId ?? 0)
    && request.submodeId === (state.saved.submodeId ?? null)
    && request.window.start === (state.saved.window ?? request.window).start
    && request.window.end === (state.saved.window ?? request.window).end
    && request.window.timezone === (state.saved.window ?? request.window).timezone
}

function savedTrajectoryMatchesRequest(
  value: RichTrajectoryEnvelope,
  saved: TimelineSavedBaseline,
  request: TimelinePreviewRequest,
): boolean {
  return value.revision_scope === 'saved'
    && value.draft_revision === null
    && value.room.toLowerCase().includes(request.room.location === 'veg' ? 'vegetation' : request.room.location)
    && value.base_config_revision === saved.baseConfigRevision
    && sameWindow(request.window, value.window)
}

export function useTimelineDraft({ saved, publicationPort }: UseTimelineDraftOptions): TimelineDraftController {
  const [state, setState] = useState(() => createTimelineDraft(saved))
  const [preview, setPreview] = useState<TimelinePreviewState>({ kind: 'idle' })
  const previewToken = useRef(0)
  const applyInFlight = useRef(false)

  const editPeriods = useCallback((periods: readonly ClimatePeriod[]) => {
    previewToken.current += 1
    setState((current) => updateTimelineDraftPeriods(current, periods))
    setPreview({ kind: 'idle' })
  }, [])

  const editPhotoperiod = useCallback((photoperiod: TimelinePhotoperiod) => {
    previewToken.current += 1
    setState((current) => updateTimelineDraftPhotoperiod(current, photoperiod))
    setPreview({ kind: 'idle' })
  }, [])

  const review = useCallback(async () => {
    const reviewed = reviewTimelineDraft(state)
    const request = requestFor(reviewed)
    const requestToken = previewToken.current + 1
    previewToken.current = requestToken
    setState(reviewed)
    setPreview({ kind: 'loading', draftRevision: request.draftRevision })
    try {
      const value = await publicationPort.preview(request)
      if (previewToken.current === requestToken) {
        if (!previewMatchesRequest(value, request)) {
          setPreview({ kind: 'failed', draftRevision: request.draftRevision })
          return
        }
        setPreview({ kind: 'ready', draftRevision: request.draftRevision, value, request, source: 'preview' })
      }
    } catch {
      if (previewToken.current === requestToken) {
        setPreview({ kind: 'failed', draftRevision: request.draftRevision })
      }
    }
  }, [publicationPort, state])

  const apply = useCallback(async () => {
    if (applyInFlight.current) return
    if (state.status.kind !== 'reviewed' || state.status.draftRevision !== state.draftRevision) return
    if (preview.kind !== 'ready' || preview.draftRevision !== state.draftRevision) return
    if (!requestMatchesState(preview.request, state)) return
    applyInFlight.current = true
    const applyToken = previewToken.current
    try {
      const savedBaseline = await publicationPort.apply(preview.request)
      if (previewToken.current !== applyToken) return
      setState(applyTimelineDraft(savedBaseline))
      if (savedBaseline.trajectory && savedTrajectoryMatchesRequest(savedBaseline.trajectory, savedBaseline, preview.request)) {
        setPreview({ ...preview, value: savedBaseline.trajectory, source: 'saved' })
      } else {
        setPreview(preview)
      }
    } catch (error) {
      if (previewToken.current !== applyToken) return
      if (error instanceof TimelineConflictError) {
        previewToken.current += 1
        setState((current) => markTimelineDraftConflict(current))
        setPreview({ kind: 'idle' })
        return
      }
      throw error
    } finally {
      applyInFlight.current = false
    }
  }, [preview, publicationPort, state])

  const discard = useCallback(() => {
    previewToken.current += 1
    setState((current) => discardTimelineDraft(current))
    setPreview({ kind: 'idle' })
  }, [])

  const switchRoom = useCallback((nextSaved: TimelineSavedBaseline): TimelineRoomSwitchResult => {
    const result = requestTimelineRoomSwitch(state, nextSaved)
    if (result.kind === 'requires-discard') return result.kind
    previewToken.current += 1
    setState(result.state)
    setPreview({ kind: 'idle' })
    return result.kind
  }, [state])

  const confirmRoomSwitch = useCallback((nextSaved: TimelineSavedBaseline) => {
    previewToken.current += 1
    setState(createTimelineDraft(nextSaved))
    setPreview({ kind: 'idle' })
  }, [])

  return {
    state,
    preview,
    editPeriods,
    editPhotoperiod,
    review,
    apply,
    discard,
    switchRoom,
    confirmRoomSwitch,
  }
}
