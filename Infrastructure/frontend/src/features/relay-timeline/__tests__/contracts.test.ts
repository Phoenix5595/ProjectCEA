import { describe, expect, it, vi } from 'vitest'

import {
  MonitoringApi,
  type MonitoringClient,
  type MonitoringRequestOptions,
} from '../../monitoring/api'
import { RelayTimelineResponse } from '../contracts'

const START = '2026-09-25T12:00:00.000Z'
const END = '2026-09-25T13:00:00.000Z'

function responseFixture() {
  return {
    range: { start: START, end: END },
    transitions: [],
    anchors: [],
    load: [],
    coverage_complete: true,
    last_heartbeat_at: null,
    watermark: 7,
    has_more: false,
    next_cursor: null,
  }
}

describe('relay timeline Zod contract', () => {
  it('parses strict half-open range timestamps once into Dates', () => {
    const result = RelayTimelineResponse.parse(responseFixture())

    expect(result.range.start).toBeInstanceOf(Date)
    expect(result.range.start.toISOString()).toBe(START)
    expect(result.range.end.toISOString()).toBe(END)
    expect(result.watermark).toBe(7)
  })
  it('rejects ranges shorter than five minutes or longer than seven days', () => {
    expect(() =>
      RelayTimelineResponse.parse({
        ...responseFixture(),
        range: { start: '2026-09-25T12:56:00.000Z', end: END },
      })
    ).toThrow()
    expect(() =>
      RelayTimelineResponse.parse({
        ...responseFixture(),
        range: { start: START, end: '2026-10-03T12:00:00.000Z' },
      })
    ).toThrow()
  })

  it('rejects unmodeled response fields and malformed heartbeat facts', () => {
    expect(() => RelayTimelineResponse.parse({ ...responseFixture(), unexpected: true })).toThrow()
    expect(() =>
      RelayTimelineResponse.parse({
        ...responseFixture(),
        transitions: [
          {
            observation_id: 1,
            observed_at: START,
            channel: 0,
            observed_state: false,
            reason: 'heartbeat',
            session_id: 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a001',
            registry_version: 1,
            device_id: null,
            device_name: null,
            device_type: null,
            location: null,
            cluster: null,
          },
        ],
      })
    ).toThrow()
  })
})

describe('MonitoringApi.relayTimeline', () => {
  it('uses the monitoring base client, range-bound cursor query, and abort options', async () => {
    const parsed = RelayTimelineResponse.parse(responseFixture())
    const get = vi.fn().mockResolvedValue(parsed)
    const options: MonitoringRequestOptions = { signal: new AbortController().signal }
    const client = { get } as unknown as MonitoringClient
    const api = new MonitoringApi(undefined, client)

    await api.relayTimeline('Veg Room', START, END, 2_000, 'fixture:2000', options)

    expect(get).toHaveBeenCalledWith(
      `/api/monitoring/control/Veg%20Room/relay-timeline?start=${encodeURIComponent(START)}&end=${encodeURIComponent(END)}&limit=2000&cursor=fixture%3A2000`,
      RelayTimelineResponse,
      options
    )
  })
})
