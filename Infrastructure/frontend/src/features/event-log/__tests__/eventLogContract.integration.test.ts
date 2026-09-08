import { readFile } from 'node:fs/promises'
import { describe, expect, it } from 'vitest'
import { parseSse } from '../api/sseParser'
import { EventLogStore, type EventLogEntry } from '../state/eventLogStore'

const encoder = new TextEncoder()
const eventModules = ['../api/sseParser.ts', '../state/eventLogStore.ts', '../state/eventLogTransport.ts', '../state/useEventLog.ts']

async function eventSources(): Promise<readonly string[]> {
  return Promise.all(eventModules.map((module) => readFile(new URL(module, import.meta.url), 'utf8')))
}

function entry(index: number): EventLogEntry {
  return {
    redisId: `${index + 1}-0`,
    eventId: `event-${index}`,
    type: 'relay.state_changed',
    category: 'relay',
    occurredAt: new Date('2026-09-03T12:00:00.000Z'),
    payload: { room: index % 4 === 0 ? 'Flower Room' : 'Vegetation Room', cluster: 'main' },
  }
}

function stream(entries: readonly EventLogEntry[]): ReadableStream<Uint8Array> {
  const frames = entries.map((item) =>
    `id: ${item.redisId}\nevent: operational_event\ndata: {"event_id":"${item.eventId}"}\n\n`,
  )
  return new ReadableStream({
    start(controller) {
      for (const frame of frames) {
        const midpoint = Math.floor(frame.length / 2)
        controller.enqueue(encoder.encode(frame.slice(0, midpoint)))
        controller.enqueue(encoder.encode(frame.slice(midpoint)))
      }
      controller.close()
    },
  })
}

async function parsedIds(body: ReadableStream<Uint8Array>): Promise<readonly string[]> {
  const ids: string[] = []
  for await (const frame of parseSse(body)) {
    if (frame.event === 'operational_event' && frame.id !== null) ids.push(frame.id)
  }
  return ids
}

describe('event-log end-to-end contract', () => {
  it('keeps 100 bootstrap, stream, and reconnect events unique and ordered', async () => {
    // Given: sixty bootstrap events, forty live events, and a ten-event reconnect overlap.
    const events = Array.from({ length: 100 }, (_, index) => entry(index))
    const store = new EventLogStore(100)

    // When: real SSE parsing feeds the same store across stream and reconnect boundaries.
    store.merge(events.slice(0, 60))
    const liveIds = await parsedIds(stream(events.slice(60)))
    const reconnectIds = await parsedIds(stream(events.slice(90)))
    store.merge([...liveIds, ...reconnectIds].map((redisId) => events[Number(redisId.split('-')[0]) - 1]))

    // Then: IDs have no gap or duplicate, retain Redis order, and yield the exact room subset.
    expect(store.snapshot().entries.map((item) => item.eventId)).toEqual(
      events.map((item) => item.eventId),
    )
    expect(
      store.snapshot().entries.filter((item) => item.payload.room === 'Flower Room'),
    ).toHaveLength(25)
  })

  it('does not emit a disconnected partial SSE frame', async () => {
    // Given: a client disconnecting after a frame header but before its terminating blank line.
    const partial = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('id: 101-0\nevent: operational_event\ndata: {}'))
        controller.close()
      },
    })

    // When: the production SSE parser reaches end-of-stream.
    const ids = await parsedIds(partial)

    // Then: no malformed partial event enters the store.
    expect(ids).toEqual([])
  })

  it('keeps event transport and state outside every control-write and hardware boundary', async () => {
    // Given: all modules that implement browser event transport and state.
    const sources = await eventSources()

    // When: their static imports and call sites are examined as the observability boundary.
    const source = sources.join('\n')

    // Then: none can reach a write client or relay/dimmer hardware API.
    expect(source).not.toMatch(/from\s+['"][^'"]*(hardware|control|relay|device|services\/api)[^'"]*['"]/)
    expect(source).not.toMatch(/\b(?:client|api|automationApi|hardware)\.(post|put|patch|delete)\s*\(/)
    expect(source).not.toMatch(/\b(set_channel|set_intensity|write_to_hardware)\s*\(/)
  })

  it('uses the history bootstrap and header-only authentication contract', async () => {
    // Given: the module that owns the browser event transport connection.
    const source = (await eventSources())[2]

    // When: its machine-consumed route and authentication wiring are inspected.

    // Then: it loads history first and never puts an API credential into an SSE URL.
    expect(source).toContain('/api/events/history')
    expect(source).toContain('INITIAL_LIMIT = 200')
    expect(source).toContain('X-API-Key')
    expect(source).not.toMatch(/params\.set\(['"]token['"]/)
  })
})
