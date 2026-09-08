import { describe, expect, it } from 'vitest'
import { parseSse } from '../api/sseParser'

async function collect<T>(stream: AsyncIterable<T>): Promise<T[]> { const result: T[] = []; for await (const item of stream) result.push(item); return result }

describe('SSE parser', () => {
  it('surfaces comment-only heartbeat frames', async () => {
    // Given: the server's idle heartbeat comment
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode(': heartbeat\n\n')); controller.close() } })

    // When: the stream is parsed
    const frames = await collect(parseSse(body))

    // Then: transport can treat the heartbeat as activity
    expect(frames).toEqual([{ id: null, event: 'comment', data: 'heartbeat' }])
  })

  it('parses CRLF multiline frames split across UTF-8 chunks', async () => {
    // Given: an emoji payload split across chunks
    const bytes = new TextEncoder().encode(': ok\r\nid: 2-0\r\nevent: operational_event\r\ndata: {"x":"🌱"}\r\ndata: {"y":1}\r\n\r\n')
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(bytes.slice(0, 43)); controller.enqueue(bytes.slice(43)); controller.close() } })
    // When: the stream is parsed
    const frames = await collect(parseSse(body))
    // Then: framing and UTF-8 are retained
    expect(frames).toEqual([{ id: '2-0', event: 'operational_event', data: '{"x":"🌱"}\n{"y":1}' }])
  })
})
