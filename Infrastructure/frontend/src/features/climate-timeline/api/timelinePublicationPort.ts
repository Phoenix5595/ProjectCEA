import type { RichTrajectoryEnvelope } from './contracts'
import type {
  TimelineOwnedValues,
  TimelineRoom,
  TimelineSavedBaseline,
  TimelineWindow,
} from '../state/timelineDraft'

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

export interface TimelinePublicationPort {
  preview(request: TimelinePreviewRequest): Promise<RichTrajectoryEnvelope>
  apply(request: TimelineApplyRequest): Promise<TimelineSavedBaseline>
}

export class TimelineConflictError extends Error {
  public readonly status = 409

  public constructor(readonly requestId?: string) {
    super('The saved timeline revision changed before Apply.')
    this.name = 'TimelineConflictError'
  }
}

export class TimelinePreviewIdentityError extends Error {
  public constructor(readonly requestId: string) {
    super('The preview response does not match the requested draft identity.')
    this.name = 'TimelinePreviewIdentityError'
  }
}
