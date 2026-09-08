import { globalEventLogStore, type EventLogPaging } from './eventLogStore'
import { parseSse } from '../api/sseParser'
import { AUTOMATION_API_URL, CEA_API_KEY } from '../../../config/env'
import { toEventLogEntry, type OperationalEvent, type OperationalEventHistory, type OperationalEventCursorReset } from './eventLogTypes'

const INITIAL_LIMIT = 200
const STALE_TIMEOUT_MS = 45_000
const RECONNECT_MIN_MS = 1_000
const RECONNECT_MAX_MS = 30_000

interface ConnectionState {
  refCount: number
  abortController: AbortController | null
  lastCursor: string | null
  authPaused: boolean
  staleTimer: ReturnType<typeof setTimeout> | null
  reconnectTimer: ReturnType<typeof setTimeout> | null
}

const state: ConnectionState = {
  refCount: 0,
  abortController: null,
  lastCursor: null,
  authPaused: false,
  staleTimer: null,
  reconnectTimer: null,
}

export interface LoadHistoryResult {
  newestCursor: string | null
  oldestCursor: string | null
  hasMore: boolean
}

type HistoryFetchOptions = {
  before?: string
  limit?: number
}

function authHeaders(accept = 'application/json'): Record<string, string> {
  const headers: Record<string, string> = { Accept: accept }
  if (CEA_API_KEY) headers['X-API-Key'] = CEA_API_KEY
  return headers
}

function clearTimers(): void {
  if (state.staleTimer) {
    clearTimeout(state.staleTimer)
    state.staleTimer = null
  }
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer)
    state.reconnectTimer = null
  }
}

let reconnectDelayForTesting: number | null = null

function jitteredReconnectDelay(): number {
  if (reconnectDelayForTesting !== null) return reconnectDelayForTesting
  return RECONNECT_MIN_MS + Math.floor(Math.random() * (RECONNECT_MAX_MS - RECONNECT_MIN_MS + 1))
}

export function _setReconnectDelayForTesting(delay: number | null): void {
  reconnectDelayForTesting = delay
}

function scheduleReconnect(): void {
  clearTimers()
  if (state.authPaused || state.refCount === 0) return
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null
    if (state.refCount > 0) void connectToEventStream()
  }, jitteredReconnectDelay())
}

function setPaging(result: LoadHistoryResult, loadingOlder: boolean): void {
  const paging: EventLogPaging = {
    oldestCursor: result.oldestCursor,
    hasMore: result.hasMore,
    loadingOlder,
  }
  globalEventLogStore.setPaging(paging)
}

async function loadHistory(options: HistoryFetchOptions = {}): Promise<LoadHistoryResult> {
  const url = new URL(`${AUTOMATION_API_URL}/api/events/history`)
  url.searchParams.set('limit', String(options.limit ?? INITIAL_LIMIT))
  if (options.before) url.searchParams.set('before', options.before)

  const response = await fetch(url.toString(), { headers: authHeaders() })

  if (response.status === 401 || response.status === 403) {
    state.authPaused = true
    throw new Error(`Event history auth failed: ${response.status}`)
  }
  if (!response.ok) {
    throw new Error(`Event history failed: ${response.status}`)
  }

  const history: OperationalEventHistory = await response.json()
  const entries = history.items.map((item) => toEventLogEntry(item.redis_id, item.event))
  globalEventLogStore.merge(entries)
  const result = {
    newestCursor: history.newest_cursor,
    oldestCursor: history.oldest_cursor,
    hasMore: history.has_more,
  }
  setPaging(result, globalEventLogStore.snapshot().paging.loadingOlder)
  return result
}

async function loadOlder(): Promise<void> {
  const paging = globalEventLogStore.snapshot().paging
  if (paging.loadingOlder || !paging.hasMore || paging.oldestCursor === null) return
  globalEventLogStore.setPaging({ ...paging, loadingOlder: true })
  try {
    await loadHistory({ before: paging.oldestCursor })
  } finally {
    const updatedPaging = globalEventLogStore.snapshot().paging
    globalEventLogStore.setPaging({ ...updatedPaging, loadingOlder: false })
  }
}

function resetStaleTimer(onStale: () => void): void {
  if (state.staleTimer) clearTimeout(state.staleTimer)
  state.staleTimer = setTimeout(() => {
    state.staleTimer = null
    onStale()
  }, STALE_TIMEOUT_MS)
}

async function connectToEventStream(): Promise<void> {
  if (state.refCount === 0 || state.authPaused || state.abortController) return

  const controller = new AbortController()
  state.abortController = controller
  clearTimers()

  try {
    const history = await loadHistory()
    if (history.newestCursor) state.lastCursor = history.newestCursor
    let resetApplied = false
    let response: Response
    while (true) {
      const params = new URLSearchParams()
      params.set('after', state.lastCursor ?? '0-0')
      response = await fetch(`${AUTOMATION_API_URL}/api/events/stream?${params.toString()}`, {
        signal: controller.signal,
        headers: authHeaders('text/event-stream'),
      })
      if (response.status !== 409 || resetApplied) break
      const reset: OperationalEventCursorReset = await response.json()
      resetApplied = true
      state.lastCursor = reset.earliest_cursor
      globalEventLogStore.reset()
      const replacementHistory = await loadHistory()
      state.lastCursor = replacementHistory.newestCursor
    }
    if (response.status === 401 || response.status === 403) {
      state.authPaused = true
      return
    }
    if (!response.ok || !response.body) {
      throw new Error(`Event stream failed: ${response.status}`)
    }

    let stale = false
    const markStale = () => {
      stale = true
      controller.abort()
    }
    resetStaleTimer(markStale)
    for await (const frame of parseSse(response.body)) {
      if (stale) break
      if (frame.event === 'operational_event' && frame.data && frame.id) {
        try {
          const event: OperationalEvent = JSON.parse(frame.data)
          const entry = toEventLogEntry(frame.id, event)
          globalEventLogStore.merge([entry])
          state.lastCursor = frame.id
        } catch {
          // Skip malformed events
        }
      } else if (frame.id) {
        state.lastCursor = frame.id
      }
      resetStaleTimer(markStale)
    }

    scheduleReconnect()
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      if (state.refCount > 0) scheduleReconnect()
      return
    }
    if (state.authPaused) return
    scheduleReconnect()
  } finally {
    if (state.abortController === controller) state.abortController = null
  }
}

function addConnectionRef(): void {
  state.refCount += 1
  if (state.refCount === 1) {
    void connectToEventStream()
  }
}

function removeConnectionRef(): void {
  state.refCount = Math.max(0, state.refCount - 1)
  if (state.refCount === 0) {
    clearTimers()
    state.abortController?.abort()
    state.abortController = null
  }
}

function getActiveConnections(): number {
  return state.refCount
}

function getAbortController(): AbortController | null {
  return state.abortController
}

function resetTransportState(): void {
  clearTimers()
  state.lastCursor = null
  state.authPaused = false
  state.refCount = 0
  state.abortController?.abort()
  state.abortController = null
  reconnectDelayForTesting = null
}

function getLastCursor(): string | null {
  return state.lastCursor
}

export {
  addConnectionRef,
  removeConnectionRef,
  loadHistory,
  loadOlder,
  getActiveConnections,
  getAbortController,
  getLastCursor,
  resetTransportState,
}
