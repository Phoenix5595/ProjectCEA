import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { RichTrajectoryEnvelope } from '../api/contracts'

export type TimelineRoom = {
  readonly location: string
  readonly cluster: string
}

export type TimelinePhotoperiod = {
  readonly dayStartTime: string
  readonly nightStartTime: string
  readonly rampUpMinutes: number
  readonly rampDownMinutes: number
}

export type TimelineWindow = {
  readonly start: string
  readonly end: string
  readonly timezone: string
}

export type TimelineOwnedValues = {
  readonly periods: readonly ClimatePeriod[]
  readonly photoperiod: TimelinePhotoperiod
}

export type TimelineSavedBaseline = TimelineOwnedValues & {
  readonly room: TimelineRoom
  readonly baseConfigRevision: string
  readonly modeId?: number
  readonly submodeId?: number | null
  readonly window?: TimelineWindow
  readonly trajectory?: RichTrajectoryEnvelope
}

export type TimelineDraftStatus =
  | { readonly kind: 'editing' }
  | { readonly kind: 'reviewed'; readonly draftRevision: number }
  | { readonly kind: 'conflict' }

export type TimelineDraft = {
  readonly saved: TimelineSavedBaseline
  readonly draft: TimelineOwnedValues
  readonly draftRevision: number
  readonly status: TimelineDraftStatus
}

export type TimelineRoomSwitch =
  | { readonly kind: 'switched'; readonly state: TimelineDraft }
  | { readonly kind: 'requires-discard'; readonly nextSaved: TimelineSavedBaseline }

function copyPeriod(period: ClimatePeriod): ClimatePeriod {
  return { ...period }
}

function copyValues(values: TimelineOwnedValues): TimelineOwnedValues {
  return {
    periods: values.periods.map(copyPeriod),
    photoperiod: { ...values.photoperiod },
  }
}

function valuesMatch(left: TimelineOwnedValues, right: TimelineOwnedValues): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function createTimelineDraft(saved: TimelineSavedBaseline): TimelineDraft {
  return {
    saved: {
      ...saved,
      room: { ...saved.room },
      ...copyValues(saved),
    },
    draft: copyValues(saved),
    draftRevision: 0,
    status: { kind: 'editing' },
  }
}

export function isTimelineDraftDirty(state: TimelineDraft): boolean {
  return !valuesMatch(
    { periods: state.saved.periods, photoperiod: state.saved.photoperiod },
    state.draft,
  )
}

export function updateTimelineDraftPeriods(
  state: TimelineDraft,
  periods: readonly ClimatePeriod[],
): TimelineDraft {
  return {
    ...state,
    draft: { ...state.draft, periods: periods.map(copyPeriod) },
    draftRevision: state.draftRevision + 1,
    status: { kind: 'editing' },
  }
}

export function updateTimelineDraftPhotoperiod(
  state: TimelineDraft,
  photoperiod: TimelinePhotoperiod,
): TimelineDraft {
  return {
    ...state,
    draft: { ...state.draft, photoperiod: { ...photoperiod } },
    draftRevision: state.draftRevision + 1,
    status: { kind: 'editing' },
  }
}

export function reviewTimelineDraft(state: TimelineDraft): TimelineDraft {
  return {
    ...state,
    status: { kind: 'reviewed', draftRevision: state.draftRevision },
  }
}

export function discardTimelineDraft(state: TimelineDraft): TimelineDraft {
  return {
    ...state,
    draft: copyValues(state.saved),
    draftRevision: state.draftRevision + 1,
    status: { kind: 'editing' },
  }
}

export function markTimelineDraftConflict(state: TimelineDraft): TimelineDraft {
  return { ...state, status: { kind: 'conflict' } }
}

export function applyTimelineDraft(saved: TimelineSavedBaseline): TimelineDraft {
  return createTimelineDraft(saved)
}

export function requestTimelineRoomSwitch(
  state: TimelineDraft,
  nextSaved: TimelineSavedBaseline,
): TimelineRoomSwitch {
  if (isTimelineDraftDirty(state)) {
    return { kind: 'requires-discard', nextSaved }
  }
  return { kind: 'switched', state: createTimelineDraft(nextSaved) }
}
