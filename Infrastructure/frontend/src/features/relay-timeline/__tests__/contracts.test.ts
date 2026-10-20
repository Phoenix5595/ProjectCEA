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
            time: START,
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

  it('parses requested-percent extrema as optional nullable 0..100 values', () => {
    const load = (bounds?: { min: number | null; max: number | null }): unknown => ({
      device_id: 101,
      device_name: 'veg_heater',
      time: START,
      requested_percent: 40,
      aggregated: true,
      interval_seconds: 60,
      ...(bounds === undefined
        ? {}
        : { requested_min_percent: bounds.min, requested_max_percent: bounds.max }),
    })

    // Older raw responses without the fields remain accepted; explicit nulls
    // say the range is unknown; both sides must stay inside 0..100.
    expect(RelayTimelineResponse.parse({ ...responseFixture(), load: [load()] }).load[0]).toEqual(
      expect.objectContaining({ requested_percent: 40, aggregated: true })
    )
    expect(
      RelayTimelineResponse.parse({ ...responseFixture(), load: [load({ min: null, max: null })] })
        .load[0]
    ).toEqual(
      expect.objectContaining({ requested_min_percent: null, requested_max_percent: null })
    )
    expect(
      RelayTimelineResponse.parse({ ...responseFixture(), load: [load({ min: 30, max: 80 })] })
        .load[0]
    ).toEqual(expect.objectContaining({ requested_min_percent: 30, requested_max_percent: 80 }))
    expect(() =>
      RelayTimelineResponse.parse({ ...responseFixture(), load: [load({ min: 101, max: null })] })
    ).toThrow()
    expect(() =>
      RelayTimelineResponse.parse({ ...responseFixture(), load: [load({ min: null, max: -1 })] })
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
