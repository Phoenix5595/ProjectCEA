import axios from 'axios'
import { z } from 'zod/v3'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { ApiClientCore } from '../../../services/api'
import {
  TimelineConflictError,
  TimelinePreviewIdentityError,
  type TimelineApplyRequest,
  type TimelinePreviewRequest,
  type TimelinePublicationPort,
} from './timelinePublicationPort'
import { RichTrajectoryEnvelope } from './contracts'
import type { TimelineSavedBaseline, TimelineWindow } from '../state/timelineDraft'

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

const savedResponseSchema = z.object({
  config_revision: z.string().min(1),
  mode_id: z.number().int(),
  submode_id: z.number().int().nullable(),
  periods: z.array(periodSchema).min(1),
  photoperiod: photoperiodSchema,
  trajectory: RichTrajectoryEnvelope.optional(),
})

const previewResponseSchema = z.object({
  request_id: z.string().min(1),
  expected_config_revision: z.string().min(1),
  draft_revision: z.number().int().nonnegative(),
  trajectory: RichTrajectoryEnvelope,
})

const applyResponseSchema = z.object({
  request_id: z.string().min(1),
  config_revision: z.string().min(1),
  mode_id: z.number().int(),
  submode_id: z.number().int().nullable(),
  periods: z.array(periodSchema).min(1),
  photoperiod: photoperiodSchema,
})

type TimelineSavedResponse = z.infer<typeof savedResponseSchema>

export interface TimelineApi extends TimelinePublicationPort {
  getSaved(room: TimelineSavedRequest): Promise<TimelineSavedBaseline>
}

export type TimelineSavedRequest = {
  readonly location: string
  readonly cluster: string
  readonly window: TimelineWindow
}

function requestPayload(request: TimelinePreviewRequest | TimelineApplyRequest, includeWindow: boolean) {
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

function mapSavedResponse(room: TimelineRoomForApi, response: TimelineSavedResponse, window?: TimelineWindow): TimelineSavedBaseline {
  return {
    room,
    baseConfigRevision: response.config_revision,
    modeId: response.mode_id,
    submodeId: response.submode_id,
    periods: response.periods.map(mapPeriod),
    photoperiod: {
      dayStartTime: response.photoperiod.day_start_time,
      nightStartTime: response.photoperiod.night_start_time,
      rampUpMinutes: response.photoperiod.ramp_up_minutes,
      rampDownMinutes: response.photoperiod.ramp_down_minutes,
    },
    window,
    trajectory: response.trajectory,
  }
}

type TimelineRoomForApi = { readonly location: string; readonly cluster: string }

export const timelineMethods = {
  async getSaved(this: ApiClientCore, request: TimelineSavedRequest): Promise<TimelineSavedBaseline> {
    const response = await this.automationClient.get(
      `/api/climate-timeline/${encodeURIComponent(request.location)}/${encodeURIComponent(request.cluster)}`,
      { params: request.window },
    )
    return mapSavedResponse(
      { location: request.location, cluster: request.cluster },
      savedResponseSchema.parse(response.data),
    )
  },

  async preview(this: ApiClientCore, request: TimelinePreviewRequest) {
    const response = await this.automationClient.post(
      `/api/climate-timeline/${encodeURIComponent(request.room.location)}/${encodeURIComponent(request.room.cluster)}/preview`,
       requestPayload(request, true),
    )
    const parsed = previewResponseSchema.parse(response.data)
    if (
      parsed.request_id !== request.requestId ||
      parsed.expected_config_revision !== request.expectedConfigRevision ||
      parsed.draft_revision !== request.draftRevision
    ) {
      throw new TimelinePreviewIdentityError(request.requestId)
    }
    return parsed.trajectory
  },

  async apply(this: ApiClientCore, request: TimelineApplyRequest): Promise<TimelineSavedBaseline> {
    try {
      const response = await this.automationClient.post(
        `/api/climate-timeline/${encodeURIComponent(request.room.location)}/${encodeURIComponent(request.room.cluster)}/apply`,
         requestPayload(request, false),
      )
      const parsed = applyResponseSchema.parse(response.data)
       return mapSavedResponse(request.room, parsed, request.window)
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        throw new TimelineConflictError(request.requestId)
      }
      throw error
    }
  },
} satisfies TimelinePublicationPort & TimelineApi
