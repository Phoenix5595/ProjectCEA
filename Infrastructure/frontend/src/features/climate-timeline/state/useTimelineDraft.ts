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
  | { readonly kind: 'ready'; readonly draftRevision: number; readonly value: RichTrajectoryEnvelope }
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

export function useTimelineDraft({ saved, publicationPort }: UseTimelineDraftOptions): TimelineDraftController {
  const [state, setState] = useState(() => createTimelineDraft(saved))
  const [preview, setPreview] = useState<TimelinePreviewState>({ kind: 'idle' })
  const previewToken = useRef(0)

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
        setPreview({ kind: 'ready', draftRevision: request.draftRevision, value })
      }
    } catch {
      if (previewToken.current === requestToken) {
        setPreview({ kind: 'failed', draftRevision: request.draftRevision })
      }
    }
  }, [publicationPort, state])

  const apply = useCallback(async () => {
    if (state.status.kind !== 'reviewed' || state.status.draftRevision !== state.draftRevision) return
    const applyToken = previewToken.current
    try {
      const savedBaseline = await publicationPort.apply(requestFor(state))
      if (previewToken.current !== applyToken) return
      setState(applyTimelineDraft(savedBaseline))
      setPreview({ kind: 'idle' })
    } catch (error) {
      if (error instanceof TimelineConflictError) {
        setState((current) => markTimelineDraftConflict(current))
        return
      }
      throw error
    }
  }, [publicationPort, state])

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
