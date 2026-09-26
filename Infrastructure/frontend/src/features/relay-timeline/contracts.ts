import { z } from 'zod/v3'

import { utcDate } from '../monitoring/api/contracts/shared'

export const RelayObservationReason = z.enum([
  'initial',
  'state_changed',
  'stale',
  'recovered',
  'assignment_changed',
  'recording_gap',
  'heartbeat',
])
export type RelayObservationReason = z.infer<typeof RelayObservationReason>

export const RelayTimelineRange = z.object({
  start: utcDate,
  end: utcDate,
}).strict().refine((range) => {
  const duration = range.end.getTime() - range.start.getTime()
  return duration >= 5 * 60 * 1_000 && duration <= 7 * 24 * 60 * 60 * 1_000
}, {
  message: 'range must be between 5 minutes and 7 days',
})
export type RelayTimelineRange = z.infer<typeof RelayTimelineRange>

export const RelayTimelineTransition = z.object({
  observation_id: z.number().int().positive(),
  observed_at: utcDate,
  channel: z.number().int().min(0).max(15).nullable(),
  observed_state: z.boolean().nullable(),
  reason: RelayObservationReason,
  session_id: z.string().uuid(),
  registry_version: z.number().int().nonnegative(),
  device_id: z.number().int().nonnegative().nullable(),
  device_name: z.string().nullable(),
  device_type: z.string().nullable(),
  location: z.string().nullable(),
  cluster: z.string().nullable(),
}).strict().superRefine((row, context) => {
  if (row.reason === 'heartbeat') {
    if (row.channel !== null || row.observed_state !== null || row.device_id !== null || row.location !== null || row.cluster !== null) {
      context.addIssue({ code: z.ZodIssueCode.custom, message: 'heartbeat rows cannot include channel or assignment facts' })
    }
  } else if (row.channel === null) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: 'non-heartbeat rows require a channel' })
  }
})
export type RelayTimelineTransition = z.infer<typeof RelayTimelineTransition>

export const RelayTimelineLoadPoint = z.object({
  device_id: z.number().int().nonnegative().nullable(),
  device_name: z.string(),
  timestamp: utcDate,
  requested_percent: z.number().finite().min(0).max(100).nullable(),
  aggregated: z.boolean(),
  interval_seconds: z.number().int().positive(),
}).strict()
export type RelayTimelineLoadPoint = z.infer<typeof RelayTimelineLoadPoint>

/** Strict read-only response for the cursor-paged physical relay timeline. */
export const RelayTimelineResponse = z.object({
  range: RelayTimelineRange,
  transitions: z.array(RelayTimelineTransition),
  anchors: z.array(RelayTimelineTransition),
  load: z.array(RelayTimelineLoadPoint),
  coverage_complete: z.boolean(),
  last_heartbeat_at: utcDate.nullable(),
  watermark: z.number().int().nonnegative(),
  has_more: z.boolean(),
  next_cursor: z.string().nullable(),
}).strict()
export type RelayTimelineResponse = z.infer<typeof RelayTimelineResponse>
