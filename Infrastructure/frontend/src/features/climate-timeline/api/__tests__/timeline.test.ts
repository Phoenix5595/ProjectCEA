import axios from 'axios'
import { describe, expect, it, vi } from 'vitest'
import { timelineMethods, type TimelineSavedRequest } from '../timeline'
import { TimelineUnavailableError } from '../timelinePublicationPort'

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
