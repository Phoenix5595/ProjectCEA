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
  /** False when the response supplied unsaved initial photoperiod defaults. */
  readonly parametersConfigured: boolean
}

export type TimelineDraftStatus =
  | { readonly kind: 'editing' }
  | { readonly kind: 'reviewed'; readonly draftRevision: number }
  | { readonly kind: 'conflict' }

export type TimelineDraft = {
  readonly saved: TimelineSavedBaseline
  /** Initial editable values; unsaved initialization is not a user edit. */
  readonly initial: TimelineOwnedValues
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
  const owned = (values: TimelineOwnedValues) => ({
    ...values,
    periods: values.periods.map(period => ({ ...period, id: undefined })),
  })
  return JSON.stringify(owned(left)) === JSON.stringify(owned(right))
}

export function createTimelineDraft(
  saved: TimelineSavedBaseline,
  initial: TimelineOwnedValues = saved
): TimelineDraft {
  return {
    saved: {
      ...saved,
      room: { ...saved.room },
      ...copyValues(saved),
    },
    initial: copyValues(initial),
    draft: copyValues(initial),
    draftRevision: 0,
    status: { kind: 'editing' },
  }
}

export function isTimelineDraftDirty(state: TimelineDraft): boolean {
  return !valuesMatch(
    { periods: state.saved.periods, photoperiod: state.saved.photoperiod },
    state.draft
  )
}

export function isTimelineDraftEdited(state: TimelineDraft): boolean {
  return !valuesMatch(state.initial, state.draft)
}

export function updateTimelineDraftPeriods(
  state: TimelineDraft,
  periods: readonly ClimatePeriod[]
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
  photoperiod: TimelinePhotoperiod
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
    draft: copyValues(state.initial),
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
  nextSaved: TimelineSavedBaseline
): TimelineRoomSwitch {
  if (isTimelineDraftEdited(state)) {
    return { kind: 'requires-discard', nextSaved }
  }
  return { kind: 'switched', state: createTimelineDraft(nextSaved) }
}

/**
 * True when the editable values match the persisted authority, so a save has
 * nothing to commit. Unconfigured parameters and a locally initialized
 * constant row are not persisted and still need Save.
 */
export function isTimelineDraftPersisted(state: TimelineDraft): boolean {
  return !isTimelineDraftDirty(state) && state.saved.parametersConfigured
}

/**
 * Late successful commit: the server kept the reviewed values while newer
 * local edits happened. Re-anchor only the saved authority; the newer draft
 * is never marked saved by this.
 */
export function reanchorTimelineSavedBaseline(
  state: TimelineDraft,
  baseline: TimelineSavedBaseline
): TimelineDraft {
  return {
    ...state,
    saved: { ...baseline, room: { ...baseline.room }, ...copyValues(baseline) },
    initial: copyValues(baseline),
    status: { kind: 'editing' },
  }
}

export type TimelineWindowReadResult =
  | { readonly kind: 'same-revision'; readonly state: TimelineDraft }
  | { readonly kind: 'rebased'; readonly state: TimelineDraft }
  | { readonly kind: 'conflict'; readonly state: TimelineDraft }

/**
 * Map one same-profile window read onto the current draft. An equal saved
 * revision updates only window/trajectory metadata (an absent envelope never
 * stretches from the old window); a different revision replaces the complete
 * baseline only when the draft is clean, and otherwise keeps every draft
 * value and the old expected revision while marking the conflict.
 */
export function applyTimelineWindowRead(
  state: TimelineDraft,
  baseline: TimelineSavedBaseline,
  initial: TimelineOwnedValues = baseline
): TimelineWindowReadResult {
  if (baseline.baseConfigRevision === state.saved.baseConfigRevision) {
    return {
      kind: 'same-revision',
      state: {
        ...state,
        saved: {
          ...state.saved,
          window: baseline.window,
          trajectory: baseline.trajectory ?? undefined,
        },
      },
    }
  }
  if (!isTimelineDraftEdited(state)) {
    return { kind: 'rebased', state: createTimelineDraft(baseline, initial) }
  }
  const conflicted = markTimelineDraftConflict(state)
  return {
    kind: 'conflict',
    state: { ...conflicted, saved: { ...conflicted.saved, window: baseline.window } },
  }
}
