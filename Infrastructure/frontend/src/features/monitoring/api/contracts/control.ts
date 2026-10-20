/**
 * Control monitoring contracts mirroring the automation service
 * `/api/monitoring/control/{history,projection}` response shapes.
 */
import { z } from 'zod'

import { RichTrajectoryEnvelope } from './rich'
import {
  AggregationMetadata,
  FlushHealth,
  MonitoringRange,
  MonitoringWarning,
  PersistenceCursor,
  Phase,
  ProjectionMetadata,
  Quality,
  SourceCursor,
  TimelineProvenance,
  utcDate,
} from './shared'

/**
 * Which recorded control families one read may carry. `lights` rejects every
 * non-light automation family, so a stale backend cannot feed device, PID or
 * climate-target tracking into room monitoring/Historical graphs; `all` is
 * the unchanged default for Automation and generic callers.
 */
export type ControlReadScope = 'all' | 'lights'

const SampleExtrema = {
  // Recorded extrema of every sample the point/step represents: an aggregate
  // bucket carries its min/max, a raw sample repeats its own value on both
  // sides. A missing side stays null/absent so an unknown extremum is never
  // rendered as a fabricated zero.
  minimum: z.number().finite().nullable().optional(),
  maximum: z.number().finite().nullable().optional(),
} as const

/** A scalar control value on the shared UTC monitoring grid. */
export const TimelinePoint = z.object({
  time: utcDate,
  value: z.number().nullable(),
  provenance: TimelineProvenance,
  aggregation: AggregationMetadata.nullable().optional(),
  ...SampleExtrema,
})
export type TimelinePoint = z.infer<typeof TimelinePoint>

/** A value that holds from `time` until the following point. */
export const TimelineStep = z.object({
  time: utcDate,
  value: z.number().nullable(),
  provenance: TimelineProvenance,
  ...SampleExtrema,
})
export type TimelineStep = z.infer<typeof TimelineStep>

/** A linear target segment with explicit UTC endpoints. */
export const TimelineLinear = z.object({
  start: utcDate,
  end: utcDate,
  start_value: z.number(),
  end_value: z.number(),
  provenance: TimelineProvenance,
})
export type TimelineLinear = z.infer<typeof TimelineLinear>

/** Recorded or projected effective/nominal climate setpoint values. */
export const ClimateTimelinePoint = TimelinePoint.extend({
  metric: z.string().min(1),
  nominal_value: z.number().nullable(),
  ramp_progress: z.number().min(0).max(1).nullable().optional(),
  mode: z.string().nullable().optional(),
  device_name: z.string().nullable().optional(),
})
export type ClimateTimelinePoint = z.infer<typeof ClimateTimelinePoint>

/** Recorded or projected per-device effective/nominal light intensity. */
export const LightTimelinePoint = TimelinePoint.extend({
  device_name: z.string().min(1),
  nominal_value: z.number().nullable(),
  ramp_progress: z.number().min(0).max(1).nullable().optional(),
  mode: z.string().nullable().optional(),
})
export type LightTimelinePoint = z.infer<typeof LightTimelinePoint>

/** Historical automation-state observation; future device state is never fabricated. */
export const DeviceTimelinePoint = z.object({
  time: utcDate,
  provenance: TimelineProvenance,
  device_name: z.string().min(1),
  device_state: z.number(),
  device_mode: z.string().min(1),
  control_reason: z.string().min(1),
})
export type DeviceTimelinePoint = z.infer<typeof DeviceTimelinePoint>

/** Historical PID output observation; null output stays null. */
export const PidTimelinePoint = z.object({
  time: utcDate,
  provenance: TimelineProvenance,
  device_name: z.string().min(1),
  pid_output: z.number().nullable().optional(),
  duty_cycle_percent: z.number().min(0).max(100).nullable().optional(),
  // Aggregate bucket extrema over the recorded samples the point represents;
  // left unclamped so a represented sample can never vanish from the range.
  pid_output_min: z.number().finite().nullable().optional(),
  pid_output_max: z.number().finite().nullable().optional(),
  duty_cycle_percent_min: z.number().finite().nullable().optional(),
  duty_cycle_percent_max: z.number().finite().nullable().optional(),
})
export type PidTimelinePoint = z.infer<typeof PidTimelinePoint>

/** A historical or projected room photoperiod phase transition. */
export const PhotoperiodTimelinePoint = z.object({
  time: utcDate,
  phase: Phase,
  provenance: TimelineProvenance,
  mode_id: z.number().nullable().optional(),
  submode_id: z.number().nullable().optional(),
  runtime_snapshot_version: z.number().nullable().optional(),
})
export type PhotoperiodTimelinePoint = z.infer<typeof PhotoperiodTimelinePoint>

/** Shared projection completeness invariant for climate and light timelines. */
export const ProjectableTimelineSeries = z.object({
  name: z.string().min(1),
  metric: z.string().min(1).nullable().optional(),
  trajectory_kind: z.enum(['scheduled', 'effective']).nullable().optional(),
  provenance: TimelineProvenance,
  projection: ProjectionMetadata.nullable().optional(),
  warnings: z.array(MonitoringWarning),
})
export type ProjectableTimelineSeries = z.infer<typeof ProjectableTimelineSeries>

/** Climate target timeline with step and linear ramp representations. */
export const ClimateTimelineSeries = ProjectableTimelineSeries.extend({
  points: z.array(ClimateTimelinePoint),
  steps: z.array(TimelineStep),
  linear: z.array(TimelineLinear),
})
export type ClimateTimelineSeries = z.infer<typeof ClimateTimelineSeries>

/** Per-light target timeline with step and linear ramp representations. */
export const LightTimelineSeries = ProjectableTimelineSeries.extend({
  points: z.array(LightTimelinePoint),
  steps: z.array(TimelineStep),
  linear: z.array(TimelineLinear),
})
export type LightTimelineSeries = z.infer<typeof LightTimelineSeries>

/** Historical device automation states only. */
export const DeviceTimelineSeries = z.object({
  name: z.string().min(1),
  provenance: TimelineProvenance,
  points: z.array(DeviceTimelinePoint),
  warnings: z.array(MonitoringWarning),
})
export type DeviceTimelineSeries = z.infer<typeof DeviceTimelineSeries>

/** Historical PID observations only. */
export const PidTimelineSeries = z.object({
  name: z.string().min(1),
  provenance: TimelineProvenance,
  points: z.array(PidTimelinePoint),
  warnings: z.array(MonitoringWarning),
})
export type PidTimelineSeries = z.infer<typeof PidTimelineSeries>

/** Version echoed by the atomic current/future publication pair. */
export const PublicationVersion = z.object({
  contract_version: z.literal(1),
  config_version: z.number().int().positive(),
  revision: z.string().regex(/^[0-9a-f]{7,64}$/),
})
export type PublicationVersion = z.infer<typeof PublicationVersion>

/** One estimated or unavailable value over a canonical future interval. */
export const ProjectionSeriesPoint = z.object({
  series_id: z.object({ value: z.string().min(1) }),
  value: z.number().nullable(),
  quality: Quality,
  valid_from: utcDate,
  valid_until: utcDate,
})
export type ProjectionSeriesPoint = z.infer<typeof ProjectionSeriesPoint>

/** One validated interval from the automation-owned future publication. */
export const FutureProjection = z.object({
  version: PublicationVersion,
  generated_at: utcDate,
  valid_from: utcDate,
  valid_until: utcDate,
  series: z.array(ProjectionSeriesPoint),
})
export type FutureProjection = z.infer<typeof FutureProjection>

/** One observed current fact mirroring `shared.monitoring_contracts.CurrentSeriesPoint`. */
export const CurrentSeriesPoint = z.object({
  series_id: z.object({ value: z.string().min(1) }),
  observed_at: utcDate,
  valid_until: utcDate,
  value: z.number().nullable(),
  quality: Quality,
})
export type CurrentSeriesPoint = z.infer<typeof CurrentSeriesPoint>

/** Resolved photoperiod phase with quality, mirroring the shared contract. */
export const PhotoperiodPublication = z
  .object({
    observed_at: utcDate,
    valid_until: utcDate,
    phase: Phase,
    quality: Quality,
  })
  .superRefine((value, ctx) => {
    if (value.phase === 'UNKNOWN') {
      if (value.quality !== 'unavailable') {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'unknown photoperiod must be unavailable',
        })
      }
    } else if (value.quality === 'unavailable') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'known photoperiod must be exact or estimated',
      })
    }
  })
export type PhotoperiodPublication = z.infer<typeof PhotoperiodPublication>

/** Fresh completed-tick control facts for one room, plus its publication version. */
export const CurrentSnapshot = z
  .object({
    version: PublicationVersion,
    observed_at: utcDate,
    valid_until: utcDate,
    series: z.array(CurrentSeriesPoint),
    photoperiod: PhotoperiodPublication.nullable(),
    persistence: PersistenceCursor,
  })
  .superRefine((value, ctx) => {
    if (value.valid_until.getTime() < value.observed_at.getTime()) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'valid_until must not precede observed_at',
      })
    }
    const ids = new Set(value.series.map(point => point.series_id.value))
    if (ids.size !== value.series.length) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'current snapshot series identifiers must be unique',
      })
    }
  })
export type CurrentSnapshot = z.infer<typeof CurrentSnapshot>

/** Read-only route response for the fresh current publication. */
export const CurrentPublicationResponse = z.object({
  quality: Quality,
  value: CurrentSnapshot.nullable(),
})
export type CurrentPublicationResponse = z.infer<typeof CurrentPublicationResponse>

/** Read-only route response for canonical future projections. */
export const ProjectionPublicationResponse = z.object({
  quality: Quality,
  value: z.array(FutureProjection),
  trajectory: RichTrajectoryEnvelope.nullable().optional(),
})
export type ProjectionPublicationResponse = z.infer<typeof ProjectionPublicationResponse>

/** History envelope containing immutable recorded and projected timelines. */
export const ControlMonitoringResponse = z.object({
  range: MonitoringRange,
  runtime_snapshot_version: z.number().int(),
  requested_max_points: z.number().int().min(10).max(100_000).nullable().optional(),
  interval_seconds: z.number().int().positive().nullable().optional(),
  cursors: z.array(SourceCursor),
  flush_health: z.array(FlushHealth),
  climate: z.array(ClimateTimelineSeries),
  lights: z.array(LightTimelineSeries),
  devices: z.array(DeviceTimelineSeries),
  pid: z.array(PidTimelineSeries),
  photoperiod: z.array(PhotoperiodTimelinePoint),
})
export type ControlMonitoringResponse = z.infer<typeof ControlMonitoringResponse>

/**
 * Parse contract for `/api/monitoring/control/{room}/history` and `.../tail`,
 * bound to the requested read scope. A `lights` response that still carries
 * recorded climate, device or PID series is rejected before any caller sees
 * it, so a stale backend can never repopulate room automation tracking.
 */
export function controlMonitoringContract(
  scope: ControlReadScope
): z.ZodType<ControlMonitoringResponse> {
  if (scope === 'all') return ControlMonitoringResponse
  return ControlMonitoringResponse.superRefine((response, context) => {
    // Recorded automation families one lights read must never carry.
    const counts: Record<string, number> = {
      climate: response.climate.length,
      device: response.devices.length,
      pid: response.pid.length,
    }
    for (const [family, count] of Object.entries(counts)) {
      if (count === 0) continue
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: `lights scope control history must not return ${family} series`,
      })
    }
  })
}
