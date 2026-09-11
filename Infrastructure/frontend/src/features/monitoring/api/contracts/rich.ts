import { z } from 'zod/v3'

import { Quality, utcDate } from './shared'

const PeriodIdentity = z.object({ period_id: z.string().min(1), label: z.string().min(1) }).strict()

const SegmentSource = z.object({
  mode: z.string().min(1),
  submode: z.string().nullable(),
  period: PeriodIdentity,
  config_revision: z.string().min(1),
  draft_revision: z.string().nullable(),
}).strict()

const SegmentBase = z.object({
  start: utcDate,
  end: utcDate,
  metric: z.string().min(1),
  unit: z.string().min(1),
  trajectory_kind: z.enum(['scheduled', 'effective']),
  quality: Quality,
  source: SegmentSource,
}).strict()

const StepTrajectorySegment = SegmentBase.extend({
  shape: z.literal('step'),
  value: z.number(),
}).strict()

const LinearTrajectorySegment = SegmentBase.extend({
  shape: z.literal('linear'),
  start_value: z.number(),
  end_value: z.number(),
}).strict()

const UnavailableTrajectorySegment = SegmentBase.extend({
  shape: z.literal('unavailable'),
  reason: z.string().min(1),
}).strict()

export const TrajectorySegment = z.discriminatedUnion('shape', [
  StepTrajectorySegment,
  LinearTrajectorySegment,
  UnavailableTrajectorySegment,
])
export type TrajectorySegment = z.infer<typeof TrajectorySegment>

const TimelineWarning = z.object({ code: z.string().min(1), detail: z.string().min(1) }).strict()

export const RichTrajectoryEnvelope = z.object({
  contract_version: z.literal(1),
  room: z.string().min(1),
  generated_at: utcDate,
  window: z.object({ start: utcDate, end: utcDate, timezone: z.string().min(1) }).strict(),
  revision_scope: z.enum(['saved', 'draft']),
  base_config_revision: z.string().min(1),
  draft_revision: z.string().nullable(),
  segments: z.array(TrajectorySegment).min(1),
  assumptions: z.array(z.string()),
  warnings: z.array(TimelineWarning),
}).strict()
export type RichTrajectoryEnvelope = z.infer<typeof RichTrajectoryEnvelope>
