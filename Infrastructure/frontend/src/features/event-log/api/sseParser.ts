export type SseFrame = Readonly<{ id: string | null; event: string; data: string }>

export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let id: string | null = null
  let event = 'message'
  let data: string[] = []
  let comment: string[] = []
  try {
    while (true) {
      const next = await reader.read()
      if (next.done) break
      buffer += decoder.decode(next.value, { stream: true })
      let index = buffer.indexOf('\n')
      while (index >= 0) {
        const line = buffer.slice(0, index).replace(/\r$/, '')
        buffer = buffer.slice(index + 1)
        if (line === '') {
          if (id !== null || event !== 'message' || data.length > 0) yield { id, event, data: data.join('\n') }
          else if (comment.length > 0) yield { id: null, event: 'comment', data: comment.join('\n') }
          id = null
          event = 'message'
          data = []
          comment = []
        } else if (line.startsWith(':')) {
          comment.push(line.slice(1).replace(/^ /, ''))
        } else {
          const colon = line.indexOf(':')
          const field = colon < 0 ? line : line.slice(0, colon)
          const value = (colon < 0 ? '' : line.slice(colon + 1)).replace(/^ /, '')
          if (field === 'id') id = value
          else if (field === 'event') event = value
          else if (field === 'data') data.push(value)
        }
        index = buffer.indexOf('\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}
