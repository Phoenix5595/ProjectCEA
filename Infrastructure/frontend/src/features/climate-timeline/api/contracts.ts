import { z } from 'zod/v3'

const utcDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/, 'timestamp must be UTC ISO 8601 with a trailing Z')
  .transform((value) => new Date(value))
  .refine((value) => !Number.isNaN(value.getTime()), 'timestamp is not a valid UTC date')

const finiteNumber = z.number().finite()

const UtcWindow = z
  .object({
    start: utcDate,
    end: utcDate,
    timezone: z.string().min(1),
  })
  .refine((window) => window.end > window.start, 'window end must be later than window start')

const PeriodIdentity = z.object({
  period_id: z.string().min(1),
  label: z.string().min(1),
})

const SegmentSource = z.object({
  mode: z.string().min(1),
  submode: z.string().min(1).nullable(),
  period: PeriodIdentity,
  config_revision: z.string().min(1),
  draft_revision: z.string().min(1).nullable(),
})

const SegmentBase = z.object({
  start: utcDate,
  end: utcDate,
  metric: z.string().min(1),
  unit: z.string().min(1),
  trajectory_kind: z.enum(['scheduled', 'effective']),
  quality: z.enum(['exact', 'estimated', 'unavailable']),
  source: SegmentSource,
})

const StepTrajectorySegment = SegmentBase.extend({
  shape: z.literal('step'),
  value: finiteNumber,
})

const LinearTrajectorySegment = SegmentBase.extend({
  shape: z.literal('linear'),
  start_value: finiteNumber,
  end_value: finiteNumber,
})

const UnavailableTrajectorySegment = SegmentBase.extend({
  shape: z.literal('unavailable'),
  reason: z.string().min(1),
})

const TrajectorySegment = z
  .discriminatedUnion('shape', [
    StepTrajectorySegment,
    LinearTrajectorySegment,
    UnavailableTrajectorySegment,
  ])
  .refine((segment) => segment.end > segment.start, 'segment end must be later than segment start')

const TimelineWarning = z.object({
  code: z.string().min(1),
  detail: z.string().min(1),
})

const EnvelopeBase = z.object({
  contract_version: z.literal(1),
  room: z.string().min(1),
  generated_at: utcDate,
  window: UtcWindow,
  base_config_revision: z.string().min(1),
  segments: z.array(TrajectorySegment).min(1),
  assumptions: z.array(z.string().min(1)),
  warnings: z.array(TimelineWarning),
})

const SavedTrajectoryEnvelope = EnvelopeBase.extend({
  revision_scope: z.literal('saved'),
  draft_revision: z.null(),
})

const DraftTrajectoryEnvelope = EnvelopeBase.extend({
  revision_scope: z.literal('draft'),
  draft_revision: z.string().min(1),
})

export const RichTrajectoryEnvelope = z
  .discriminatedUnion('revision_scope', [SavedTrajectoryEnvelope, DraftTrajectoryEnvelope])
  .superRefine((envelope, context) => {
    for (const segment of envelope.segments) {
      if (segment.source.config_revision !== envelope.base_config_revision) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'segment config revision must match envelope base revision',
        })
      }
      if (segment.source.draft_revision !== envelope.draft_revision) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'segment draft revision must match envelope draft revision',
        })
      }
    }
  })

export type RichTrajectoryEnvelope = z.infer<typeof RichTrajectoryEnvelope>
export type TrajectorySegment = z.infer<typeof TrajectorySegment>
