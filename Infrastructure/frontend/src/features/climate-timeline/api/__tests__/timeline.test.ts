import axios from 'axios'
import { describe, expect, it, vi } from 'vitest'
import { RichTrajectoryEnvelope } from '../contracts'
import { timelineMethods, type TimelineSavedRequest } from '../timeline'
import { TimelineUnavailableError, type TimelineApplyRequest } from '../timelinePublicationPort'

const request: TimelineSavedRequest = {
  location: 'Veg Room',
  cluster: 'main',
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

function savedEnvelopeFixture() {
  return RichTrajectoryEnvelope.parse({
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
    segments: [{
      shape: 'step',
      value: 24,
      start: '2026-01-01T00:00:00.000Z',
      end: '2026-01-01T01:00:00.000Z',
      metric: 'temperature',
      unit: 'celsius',
      trajectory_kind: 'scheduled',
      quality: 'exact',
      source: {
        mode: 'veg',
        submode: null,
        period: { period_id: 'day', label: 'Day' },
        config_revision: 'config-2',
        draft_revision: null,
      },
    }],
    assumptions: [],
    warnings: [],
  })
}

/** Raw wire payload as the backend would emit it (ISO strings, not Date objects). */
function savedResponsePayload() {
  return {
    config_revision: 'config-2',
    mode_id: 17,
    submode_id: 3,
    periods: [{
      period_name: 'Day',
      start_time: '06:00',
      end_time: '18:00',
      ramp_minutes: 30,
      heating_setpoint: 22,
      cooling_setpoint: 25,
      vpd_setpoint: 1.2,
      co2_setpoint: 900,
      details: 'saved',
    }],
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
      segments: [{
        shape: 'step',
        value: 24,
        start: '2026-01-01T00:00:00.000Z',
        end: '2026-01-01T01:00:00.000Z',
        metric: 'temperature',
        unit: 'celsius',
        trajectory_kind: 'scheduled',
        quality: 'exact',
        source: {
          mode: 'veg',
          submode: null,
          period: { period_id: 'day', label: 'Day' },
          config_revision: 'config-2',
          draft_revision: null,
        },
      }],
      assumptions: [],
      warnings: [],
    },
  }
}

function applyResponsePayload() {
  const payload = savedResponsePayload()
  const { trajectory: _trajectory, ...applyResponse } = payload
  return { request_id: 'apply-1', ...applyResponse }
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
    periods: [{
      period_name: 'Day',
      start_time: '06:00',
      end_time: '18:00',
      ramp_minutes: 30,
      heating_setpoint: 22,
      cooling_setpoint: 25,
      vpd_setpoint: 1.2,
      co2_setpoint: 900,
      details: 'saved',
    }],
    photoperiod: {
      dayStartTime: '06:00',
      nightStartTime: '18:00',
      rampUpMinutes: 20,
      rampDownMinutes: 20,
    },
  },
}

describe('timelineMethods.getSaved window round-trip', () => {
  it('maps the requested window into the saved baseline', async () => {
    // Given: the saved endpoint echoes a full aggregate for the requested window.
    const { core, get } = stubClient()
    get.mockResolvedValue({ data: savedResponsePayload() })

    // When: the saved timeline is requested.
    const baseline = await timelineMethods.getSaved.call(core, request)

    // Then: the baseline carries that exact window so previews reuse it.
    expect(get).toHaveBeenCalledWith(
      '/api/climate-timeline/Veg%20Room/main',
      { params: request.window },
    )
    expect(baseline.window).toEqual(request.window)
    expect(baseline.trajectory).toEqual(savedEnvelopeFixture())
  })
})

describe('timelineMethods.apply saved snapshot refresh', () => {
  it('returns the refetched saved baseline with its envelope for the previewed window', async () => {
    // Given: a committed apply whose response carries no envelope.
    const { core, get, post } = stubClient()
    post.mockResolvedValue({ data: applyResponsePayload() })
    get.mockResolvedValue({ data: savedResponsePayload() })

    // When: the draft is applied.
    const baseline = await timelineMethods.apply.call(core, applyRequest)

    // Then: the baseline is the authoritative saved snapshot over the same window.
    expect(get).toHaveBeenCalledWith(
      '/api/climate-timeline/Veg%20Room/main',
      { params: request.window },
    )
    expect(baseline.baseConfigRevision).toBe('config-2')
    expect(baseline.window).toEqual(request.window)
    expect(baseline.trajectory).toEqual(savedEnvelopeFixture())
  })

  it('degrades to the commit-only baseline when the saved refresh fails after a commit', async () => {
    // Given: the apply commit succeeds but the follow-up saved read fails.
    const { core, get, post } = stubClient()
    post.mockResolvedValue({ data: applyResponsePayload() })
    get.mockRejectedValue(new Error('saved read failed'))

    // When: the draft is applied.
    const baseline = await timelineMethods.apply.call(core, applyRequest)

    // Then: the committed save still surfaces with the previewed window and no envelope.
    expect(baseline.baseConfigRevision).toBe('config-2')
    expect(baseline.window).toEqual(request.window)
    expect(baseline.trajectory).toBeUndefined()
  })
})
