/**
 * Control monitoring contracts mirroring the automation service
 * `/api/monitoring/control/{history,projection}` response shapes.
 */
import { z } from 'zod/v3'
import {
  AggregationMetadata,
  FlushHealth,
  MonitoringRange,
  MonitoringWarning,
  Phase,
  ProjectionMetadata,
  Quality,
  SourceCursor,
  TimelineProvenance,
  utcDate,
} from './shared'

/** A scalar control value on the shared UTC monitoring grid. */
export const TimelinePoint = z.object({
  timestamp: utcDate,
  value: z.number().nullable(),
  provenance: TimelineProvenance,
  aggregation: AggregationMetadata.nullable().optional(),
})
export type TimelinePoint = z.infer<typeof TimelinePoint>

/** A value that holds from `timestamp` until the following point. */
export const TimelineStep = z.object({
  timestamp: utcDate,
  value: z.number().nullable(),
  provenance: TimelineProvenance,
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
  timestamp: utcDate,
  provenance: TimelineProvenance,
  device_name: z.string().min(1),
  device_state: z.number(),
  device_mode: z.string().min(1),
  control_reason: z.string().min(1),
})
export type DeviceTimelinePoint = z.infer<typeof DeviceTimelinePoint>

/** Historical PID output observation; null output stays null. */
export const PidTimelinePoint = z.object({
  timestamp: utcDate,
  provenance: TimelineProvenance,
  device_name: z.string().min(1),
  pid_output: z.number().nullable().optional(),
  duty_cycle_percent: z.number().min(0).max(100).nullable().optional(),
})
export type PidTimelinePoint = z.infer<typeof PidTimelinePoint>

/** A historical or projected room photoperiod phase transition. */
export const PhotoperiodTimelinePoint = z.object({
  timestamp: utcDate,
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

/** Read-only route response for canonical future projections. */
export const ProjectionPublicationResponse = z.object({
  quality: Quality,
  value: z.array(FutureProjection),
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
