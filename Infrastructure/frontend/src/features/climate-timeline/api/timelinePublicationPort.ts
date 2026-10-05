import type {
  TimelineOwnedValues,
  TimelineRoom,
  TimelineSavedBaseline,
  TimelineWindow,
} from '../state/timelineDraft'

import type { RichTrajectoryEnvelope } from './contracts'

export type TimelinePreviewRequest = {
  readonly room: TimelineRoom
  readonly requestId: string
  readonly expectedConfigRevision: string
  readonly draftRevision: number
  readonly modeId: number
  readonly submodeId: number | null
  readonly window: TimelineWindow
  readonly values: TimelineOwnedValues
}

export type TimelineApplyRequest = TimelinePreviewRequest

/** Validated preview result: the server echoes the exact reviewed identity. */
export type TimelinePreviewResult = {
  readonly requestId: string
  readonly expectedConfigRevision: string
  readonly draftRevision: number
  readonly modeId: number
  readonly submodeId: number | null
  readonly window: TimelineWindow
  readonly trajectory: RichTrajectoryEnvelope | null
}

/** One committed timeline save plus any post-commit refresh warning. */
export type TimelineApplyOutcome = {
  readonly baseline: TimelineSavedBaseline
  readonly warning: string | null
}

export interface TimelinePublicationPort {
  preview(request: TimelinePreviewRequest): Promise<TimelinePreviewResult>
  apply(request: TimelineApplyRequest): Promise<TimelineApplyOutcome>
}

export class TimelineConflictError extends Error {
  public readonly status = 409

  public constructor(readonly requestId?: string) {
    super('The saved timeline revision changed before Apply.')
    this.name = 'TimelineConflictError'
  }
}

export class TimelineUnavailableError extends Error {
  public readonly status = 409

  public constructor(readonly detail: string) {
    super(detail)
    this.name = 'TimelineUnavailableError'
  }
}

export class TimelinePreviewIdentityError extends Error {
  public constructor(readonly requestId: string) {
    super('The preview response does not match the requested draft identity.')
    this.name = 'TimelinePreviewIdentityError'
  }
}
