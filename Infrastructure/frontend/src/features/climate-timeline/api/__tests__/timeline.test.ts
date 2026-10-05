import axios from 'axios'
import { describe, expect, it, vi } from 'vitest'

import { timelineMethods, type TimelineSavedRequest } from '../timeline'
import {
  TimelineConflictError,
  TimelineUnavailableError,
  type TimelineApplyRequest,
} from '../timelinePublicationPort'

const request: TimelineSavedRequest = {
  location: 'Veg Room',
  cluster: 'main',
  modeId: 17,
  submodeId: 3,
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    timezone: 'UTC',
  },
}

function failedClient(error: unknown) {
  const client = axios.create()
  vi.spyOn(client, 'get').mockRejectedValue(error)
  return {
    backendClient: axios.create(),
    automationClient: client,
    weatherClient: axios.create(),
  }
}

function axiosFailure(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  })
}

describe('timelineMethods.getSaved', () => {
  it('converts only the known timeline_unavailable 409 contract to a typed error', async () => {
    // Given: the saved timeline endpoint reports its documented recoverable state.
    const error = axiosFailure(409, { code: 'timeline_unavailable', detail: 'runtime unavailable' })

    // When: the saved timeline is requested.
    const result = timelineMethods.getSaved.call(failedClient(error), request)

    // Then: callers can recover this exact contract without weakening other errors.
    await expect(result).rejects.toBeInstanceOf(TimelineUnavailableError)
  })

  it.each([
    [409, { code: 'stale_timeline_revision', detail: 'stale' }],
    [500, { code: 'timeline_unavailable', detail: 'server failure' }],
  ] as const)('rethrows an unrecognized saved timeline failure (%s)', async (status, detail) => {
    // Given: a failure that is not the documented saved-timeline availability contract.
    const error = axiosFailure(status, detail)

    // When: the saved timeline is requested.
    const result = timelineMethods.getSaved.call(failedClient(error), request)

    // Then: the original failure remains diagnosable by the room-loading boundary.
    await expect(result).rejects.toBe(error)
  })
})


/** Raw wire payload as the backend would emit it (ISO strings, not Date objects). */
function savedResponsePayload() {
  return {
    config_revision: 'config-2',
    mode_id: 17,
    submode_id: 3,
    parameters_configured: true,
    periods: [
      {
        period_name: 'Day',
        start_time: '06:00',
        end_time: '18:00',
        ramp_minutes: 30,
        heating_setpoint: 22,
        cooling_setpoint: 25,
        vpd_setpoint: 1.2,
        co2_setpoint: 900,
        details: 'saved',
      },
    ],
    photoperiod: {
      day_start_time: '06:00',
      night_start_time: '18:00',
      ramp_up_minutes: 20,
      ramp_down_minutes: 20,
    },
    trajectory: {
      contract_version: 1,
      room: 'Veg Room',
      generated_at: '2026-01-01T00:00:00.000Z',
      window: {
        start: '2026-01-01T00:00:00.000Z',
        end: '2026-01-02T00:00:00.000Z',
        timezone: 'UTC',
      },
      revision_scope: 'saved',
      base_config_revision: 'config-2',
      draft_revision: null,
      segments: [
        {
          shape: 'step',
          value: 24,
          start: '2026-01-01T00:00:00.000Z',
          end: '2026-01-01T01:00:00.000Z',
          metric: 'temperature',
          unit: 'celsius',
          trajectory_kind: 'scheduled',
          quality: 'exact',
          source: {
            mode: '17',
            submode: '3',
            period: { period_id: 'day', label: 'Day' },
            config_revision: 'config-2',
            draft_revision: null,
          },
        },
      ],
      assumptions: [],
      warnings: [],
    },
  }
}

function applyResponsePayload() {
  const payload = savedResponsePayload()
  const { trajectory: _trajectory, ...applyResponse } = payload
  void _trajectory
  return {
    request_id: 'apply-1',
    notification_warning: null,
    ...applyResponse,
  }
}

function stubClient() {
  const client = axios.create()
  const get = vi.fn()
  const post = vi.fn()
  vi.spyOn(client, 'get').mockImplementation(get)
  vi.spyOn(client, 'post').mockImplementation(post)
  return {
    client,
    get,
    post,
    core: {
      backendClient: axios.create(),
      automationClient: client,
      weatherClient: axios.create(),
    },
  }
}

const applyRequest: TimelineApplyRequest = {
  room: { location: 'Veg Room', cluster: 'main' },
  requestId: 'apply-1',
  expectedConfigRevision: 'config-1',
  draftRevision: 3,
  modeId: 17,
  submodeId: 3,
  window: request.window,
  values: {
    periods: [
      {
        period_name: 'Day',
        start_time: '06:00',
        end_time: '18:00',
        ramp_minutes: 30,
        heating_setpoint: 22,
        cooling_setpoint: 25,
        vpd_setpoint: 1.2,
        co2_setpoint: 900,
        details: 'saved',
      },
    ],
    photoperiod: {
      dayStartTime: '06:00',
      nightStartTime: '18:00',
      rampUpMinutes: 20,
      rampDownMinutes: 20,
    },
  },
}


describe('timelineMethods.preview', () => {
  it('converts a stale expected revision 409 into the typed conflict error', async () => {
    // Given: the preview endpoint reports the documented revision conflict.
    const { core, post } = stubClient()
    post.mockRejectedValue(
      axiosFailure(409, { code: 'timeline_revision_conflict', detail: 'stale' })
    )

    // When: the draft is reviewed.
    const result = timelineMethods.preview.call(core, {
      room: { location: 'Veg Room', cluster: 'main' },
      requestId: 'preview-1',
      expectedConfigRevision: 'config-1',
      draftRevision: 3,
      modeId: 17,
      submodeId: 3,
      window: request.window,
      values: {
        periods: [
          {
            period_name: 'Day',
            start_time: '06:00',
            end_time: '18:00',
            ramp_minutes: 30,
            heating_setpoint: 22,
            cooling_setpoint: 25,
            vpd_setpoint: 1.2,
            co2_setpoint: 900,
            details: 'saved',
          },
        ],
        photoperiod: {
          dayStartTime: '06:00',
          nightStartTime: '18:00',
          rampUpMinutes: 20,
          rampDownMinutes: 20,
        },
      },
    })

    // Then: the review boundary reports a typed conflict, mirroring Apply.
    await expect(result).rejects.toBeInstanceOf(TimelineConflictError)
  })
})

describe('timelineMethods.apply saved snapshot refresh', () => {
  it('does not adopt authority that changed after the save committed', async () => {
    const { core, get, post } = stubClient()
    post.mockResolvedValue({
      data: { ...applyResponsePayload(), notification_warning: 'configuration_notification_failed' },
    })
    const newer = savedResponsePayload()
    newer.config_revision = 'config-3'
    newer.photoperiod.day_start_time = '09:00'
    newer.trajectory.base_config_revision = 'config-3'
    newer.trajectory.segments[0].source.config_revision = 'config-3'
    get.mockResolvedValue({ data: newer })

    const outcome = await timelineMethods.apply.call(core, applyRequest)

    expect(outcome.baseline.baseConfigRevision).toBe('config-2')
    expect(outcome.baseline.photoperiod.dayStartTime).toBe('06:00')
    expect(outcome.baseline.trajectory).toBeUndefined()
    expect(outcome.warning).toBe(
      'configuration_notification_failed; saved_authority_changed_after_commit'
    )
  })

  it('degrades to the commit-only baseline with a refresh warning when the saved read fails', async () => {
    // Given: the apply commit succeeds but the follow-up saved read fails.
    const { core, get, post } = stubClient()
    post.mockResolvedValue({ data: applyResponsePayload() })
    get.mockRejectedValue(new Error('saved read failed'))

    // When: the draft is applied.
    const outcome = await timelineMethods.apply.call(core, applyRequest)

    // Then: the committed save still surfaces with the previewed window and no envelope.
    expect(outcome.baseline.baseConfigRevision).toBe('config-2')
    expect(outcome.baseline.window).toEqual(request.window)
    expect(outcome.baseline.trajectory).toBeUndefined()
    expect(outcome.warning).toBe('saved_projection_refresh_failed')
  })
})

describe('exact profile authority validation', () => {
  it.each(['profile', 'window', 'segment'] as const)(
    'rejects a mismatched %s instead of showing another profile',
    async mismatch => {
      const { core, get } = stubClient()
      const response = savedResponsePayload()
      if (mismatch === 'profile') response.submode_id = 8
      if (mismatch === 'window') response.trajectory.window.end = '2026-01-03T00:00:00.000Z'
      if (mismatch === 'segment') response.trajectory.segments[0].source.mode = '99'
      get.mockResolvedValue({ data: response })
      await expect(timelineMethods.getSaved.call(core, request)).rejects.toMatchObject({
        name: 'TimelinePreviewIdentityError',
      })
    }
  )
})
