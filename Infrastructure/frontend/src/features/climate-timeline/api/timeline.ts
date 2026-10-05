import axios from 'axios'
import { z } from 'zod'

import type { ApiClientCore } from '../../../services/api'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TimelineSavedBaseline, TimelineWindow } from '../state/timelineDraft'

import { RichTrajectoryEnvelope } from './contracts'
import {
  TimelineConflictError,
  TimelineUnavailableError,
  TimelinePreviewIdentityError,
  type TimelineApplyRequest,
  type TimelineApplyOutcome,
  type TimelinePreviewRequest,
  type TimelinePreviewResult,
  type TimelinePublicationPort,
} from './timelinePublicationPort'

const periodSchema = z.object({
  id: z.union([z.number().int(), z.string()]).optional(),
  period_name: z.string().min(1),
  start_time: z.string().min(1),
  end_time: z.string().min(1),
  ramp_minutes: z.number().int().nonnegative(),
  heating_setpoint: z.number().nullable(),
  cooling_setpoint: z.number().nullable(),
  vpd_setpoint: z.number().nullable(),
  co2_setpoint: z.number().int().nullable(),
  details: z.string(),
})

const photoperiodSchema = z.object({
  day_start_time: z.string().min(1),
  night_start_time: z.string().min(1),
  ramp_up_minutes: z.number().int().nonnegative(),
  ramp_down_minutes: z.number().int().nonnegative(),
})

const utcWindowSchema = z.object({
  start: z.string().min(1),
  end: z.string().min(1),
  timezone: z.string().min(1),
})

const savedResponseSchema = z.object({
  config_revision: z.string().min(1),
  mode_id: z.number().int(),
  submode_id: z.number().int().nullable(),
  periods: z.array(periodSchema),
  photoperiod: photoperiodSchema,
  trajectory: RichTrajectoryEnvelope.nullish(),
  parameters_configured: z.boolean(),
})

const previewResponseSchema = z.object({
  request_id: z.string().min(1),
  expected_config_revision: z.string().min(1),
  draft_revision: z.number().int().nonnegative(),
  mode_id: z.number().int(),
  submode_id: z.number().int().nullable(),
  window: utcWindowSchema,
  trajectory: RichTrajectoryEnvelope.nullish(),
})

const applyResponseSchema = z.object({
  request_id: z.string().min(1),
  config_revision: z.string().min(1),
  mode_id: z.number().int(),
  submode_id: z.number().int().nullable(),
  periods: z.array(periodSchema).min(1),
  photoperiod: photoperiodSchema,
  parameters_configured: z.literal(true),
  notification_warning: z.string().nullish(),
})

type TimelineSavedResponse = z.infer<typeof savedResponseSchema>

const timelineUnavailableResponseSchema = z.object({
  detail: z.object({
    code: z.literal('timeline_unavailable'),
    detail: z.string().min(1),
  }),
})

export interface TimelineApi extends TimelinePublicationPort {
  getSaved(room: TimelineSavedRequest): Promise<TimelineSavedBaseline>
  getConfiguration(room: TimelineSavedRequest): Promise<TimelineSavedBaseline>
}

export type TimelineSavedRequest = {
  readonly location: string
  readonly cluster: string
  readonly modeId: number
  readonly submodeId: number | null
  readonly window: TimelineWindow
}

function requestPayload(
  request: TimelinePreviewRequest | TimelineApplyRequest,
  includeWindow: boolean
) {
  const payload = {
    request_id: request.requestId,
    expected_config_revision: request.expectedConfigRevision,
    draft_revision: request.draftRevision,
    mode_id: request.modeId,
    submode_id: request.submodeId,
    periods: request.values.periods.map((period, index) => ({
      id: period.id != null ? String(period.id) : `period-${index + 1}`,
      period_name: period.period_name,
      start_time: period.start_time,
      end_time: period.end_time,
      ramp_minutes: period.ramp_minutes,
      heating_setpoint: period.heating_setpoint,
      cooling_setpoint: period.cooling_setpoint,
      vpd_setpoint: period.vpd_setpoint,
      co2_setpoint: period.co2_setpoint,
      details: period.details,
    })),
    photoperiod: {
      day_start_time: request.values.photoperiod.dayStartTime,
      night_start_time: request.values.photoperiod.nightStartTime,
      ramp_up_minutes: request.values.photoperiod.rampUpMinutes,
      ramp_down_minutes: request.values.photoperiod.rampDownMinutes,
    },
  }
  return includeWindow ? { ...payload, window: request.window } : payload
}

/** Fetch the exact saved profile aggregate for one window. */
async function fetchSavedBaseline(
  core: ApiClientCore,
  room: TimelineRoomForApi,
  modeId: number,
  submodeId: number | null,
  window: TimelineWindow,
  suffix: '/profile' | '/configuration'
): Promise<TimelineSavedBaseline> {
  const response = await core.automationClient.get(
    `/api/climate-timeline/${encodeURIComponent(room.location)}/${encodeURIComponent(room.cluster)}${suffix}`,
    {
      params: {
        mode_id: modeId,
        submode_id: submodeId === null ? undefined : submodeId,
        start: window.start,
        end: window.end,
        timezone: window.timezone,
      },
    }
  )
  const parsed = savedResponseSchema.parse(response.data)
  if (parsed.mode_id !== modeId || parsed.submode_id !== submodeId) {
    throw new TimelinePreviewIdentityError(`${room.location}:${room.cluster}:${modeId}:${submodeId}`)
  }
  if (
    parsed.trajectory &&
    !trajectoryMatchesProfile(
      parsed.trajectory, room, parsed.config_revision, window,
      modeId, submodeId, 'saved', null
    )
  ) {
    throw new TimelinePreviewIdentityError(`${room.location}:${room.cluster}:${modeId}:${submodeId}`)
  }
  return mapSavedResponse(room, parsed, modeId, submodeId, window)
}

function mapPeriod(raw: z.infer<typeof periodSchema>): ClimatePeriod {
  const period: ClimatePeriod = {
    period_name: raw.period_name,
    start_time: raw.start_time.slice(0, 5),
    end_time: raw.end_time.slice(0, 5),
    ramp_minutes: raw.ramp_minutes,
    heating_setpoint: raw.heating_setpoint,
    cooling_setpoint: raw.cooling_setpoint,
    vpd_setpoint: raw.vpd_setpoint,
    co2_setpoint: raw.co2_setpoint,
    details: raw.details,
  }
  return typeof raw.id === 'number' ? { ...period, id: raw.id } : period
}

function mapSavedResponse(
  room: TimelineRoomForApi,
  response: TimelineSavedResponse,
  modeId: number,
  submodeId: number | null,
  window?: TimelineWindow
): TimelineSavedBaseline {
  return {
    room,
    baseConfigRevision: response.config_revision,
    modeId,
    submodeId,
    periods: response.periods.map(mapPeriod),
    photoperiod: {
      dayStartTime: response.photoperiod.day_start_time,
      nightStartTime: response.photoperiod.night_start_time,
      rampUpMinutes: response.photoperiod.ramp_up_minutes,
      rampDownMinutes: response.photoperiod.ramp_down_minutes,
    },
    window,
    trajectory: response.trajectory ?? undefined,
    parametersConfigured: response.parameters_configured,
  }
}

type TimelineRoomForApi = { readonly location: string; readonly cluster: string }

function sameWindow(
  left: TimelineWindow,
  right: { start: string | Date; end: string | Date; timezone: string }
) {
  const start = right.start instanceof Date ? right.start.getTime() : Date.parse(right.start)
  const end = right.end instanceof Date ? right.end.getTime() : Date.parse(right.end)
  return (
    Date.parse(left.start) === start &&
    Date.parse(left.end) === end &&
    left.timezone === right.timezone
  )
}

function trajectoryMatchesProfile(
  envelope: z.infer<typeof RichTrajectoryEnvelope>,
  room: TimelineRoomForApi,
  revision: string,
  window: TimelineWindow,
  modeId: number,
  submodeId: number | null,
  revisionScope: 'saved' | 'draft',
  draftRevision: string | null
): boolean {
  if (envelope.revision_scope !== revisionScope) return false
  if (envelope.draft_revision !== draftRevision) return false
  if (envelope.room.toLowerCase() !== room.location.toLowerCase()) return false
  if (envelope.base_config_revision !== revision) return false
  if (!sameWindow(window, envelope.window)) return false
  const expectedMode = String(modeId)
  const expectedSubmode = submodeId === null ? null : String(submodeId)
  return envelope.segments.every(
    segment =>
      segment.source.mode === expectedMode && segment.source.submode === expectedSubmode
  )
}

export const timelineMethods = {
  async getSaved(
    this: ApiClientCore,
    request: TimelineSavedRequest
  ): Promise<TimelineSavedBaseline> {
    try {
      return await fetchSavedBaseline(
        this,
        { location: request.location, cluster: request.cluster },
        request.modeId,
        request.submodeId,
        request.window,
        '/profile'
      )
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        const parsed = timelineUnavailableResponseSchema.safeParse(error.response.data)
        if (parsed.success) {
          throw new TimelineUnavailableError(parsed.data.detail.detail)
        }
      }
      throw error
    }
  },

  async getConfiguration(
    this: ApiClientCore,
    request: TimelineSavedRequest
  ): Promise<TimelineSavedBaseline> {
    const baseline = await fetchSavedBaseline(
      this,
      { location: request.location, cluster: request.cluster },
      request.modeId,
      request.submodeId,
      request.window,
      '/configuration'
    )
    // The metadata-only fallback never carries a projector envelope.
    if (baseline.trajectory !== undefined) {
      throw new TimelinePreviewIdentityError(request.location)
    }
    return baseline
  },

  async preview(this: ApiClientCore, request: TimelinePreviewRequest) {
    let response
    try {
      response = await this.automationClient.post(
        `/api/climate-timeline/${encodeURIComponent(request.room.location)}/${encodeURIComponent(request.room.cluster)}/preview`,
        requestPayload(request, true)
      )
    } catch (error) {
      // A stale expected revision is a typed conflict, mirroring Apply.
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        throw new TimelineConflictError(request.requestId)
      }
      throw error
    }
    const parsed = previewResponseSchema.parse(response.data)
    if (
      parsed.request_id !== request.requestId ||
      parsed.expected_config_revision !== request.expectedConfigRevision ||
      parsed.draft_revision !== request.draftRevision ||
      parsed.mode_id !== request.modeId ||
      parsed.submode_id !== request.submodeId ||
      !sameWindow(request.window, parsed.window)
    ) {
      throw new TimelinePreviewIdentityError(request.requestId)
    }
    // A reviewed NULL-target draft is valid; only a present envelope is segment-validated.
    if (
      parsed.trajectory &&
      !trajectoryMatchesProfile(
        parsed.trajectory,
        request.room,
        request.expectedConfigRevision,
        request.window,
        request.modeId,
        request.submodeId,
        'draft',
        String(request.draftRevision)
      )
    ) {
      throw new TimelinePreviewIdentityError(request.requestId)
    }
    const result: TimelinePreviewResult = {
      requestId: parsed.request_id,
      expectedConfigRevision: parsed.expected_config_revision,
      draftRevision: parsed.draft_revision,
      modeId: parsed.mode_id,
      submodeId: parsed.submode_id,
      window: { start: parsed.window.start, end: parsed.window.end, timezone: parsed.window.timezone },
      trajectory: parsed.trajectory ?? null,
    }
    return result
  },

  async apply(this: ApiClientCore, request: TimelineApplyRequest): Promise<TimelineApplyOutcome> {
    let committed: TimelineSavedBaseline
    let committedWarning: string | null = null
    try {
      const response = await this.automationClient.post(
        `/api/climate-timeline/${encodeURIComponent(request.room.location)}/${encodeURIComponent(request.room.cluster)}/apply`,
        requestPayload(request, false)
      )
      const parsed = applyResponseSchema.parse(response.data)
      if (parsed.mode_id !== request.modeId || parsed.submode_id !== request.submodeId) {
        throw new TimelinePreviewIdentityError(request.requestId)
      }
      committed = mapSavedResponse(request.room, parsed, request.modeId, request.submodeId, request.window)
      committedWarning = parsed.notification_warning ?? null
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        throw new TimelineConflictError(request.requestId)
      }
      throw error
    }
    // Re-read the exact committed profile so the chart re-anchors to what the
    // server actually persisted. Accept the read only when the profile and the
    // committed revision still agree; otherwise fall back to the commit-only
    // baseline with an explicit warning instead of rebasing silently.
    const warnings: string[] = []
    if (committedWarning) warnings.push(committedWarning)
    try {
      const refreshed = await fetchSavedBaseline(
        this,
        request.room,
        request.modeId,
        request.submodeId,
        request.window,
        '/profile'
      )
      if (refreshed.baseConfigRevision !== committed.baseConfigRevision) {
        warnings.push('saved_authority_changed_after_commit')
      } else {
        const baseline = refreshed
        return { baseline, warning: warnings.length > 0 ? warnings.join('; ') : null }
      }
    } catch {
      warnings.push('saved_projection_refresh_failed')
    }
    return { baseline: committed, warning: warnings.length > 0 ? warnings.join('; ') : null }
  },
} satisfies TimelinePublicationPort & TimelineApi
