export const PERFORMANCE_MARKS_ENABLED = import.meta.env.VITE_MONITORING_PERF_MARKS === '1'

const ALIGNMENT_DELAY_MS = Number(import.meta.env.VITE_MONITORING_PERF_INJECT_DELAY_MS ?? 0)

export interface MonitoringPerfTick {
  readonly tickIndex: number
  readonly alignMs: number
  readonly convertMs: number
  readonly setDataMs: number
  readonly totalTickMs: number
  readonly commitMs: number
  readonly requestToPaintMs: number | null
  readonly visualAgeMs: number
}

export interface MonitoringPerfDebug {
  readonly tickCount: number
  readonly lastTickAt: number | null
  readonly samples: readonly MonitoringPerfTick[]
  readonly requestCount: number
  readonly lastResizeMs: number | null
  readonly resizeCount: number
}

interface PendingTick {
  readonly id: number
  readonly startedAt: number
  readonly requestStartedAt: number | null
  alignMs: number
  convertMs: number
  setDataMs: number
}

interface PerfState {
  nextTickId: number
  tickCount: number
  lastTickAt: number | null
  requestCount: number
  latestCompletedRequest: { readonly startedAt: number; readonly finishedAt: number } | null
  lastResizeMs: number | null
  resizeCount: number
  readonly pending: Map<number, PendingTick>
  readonly samples: MonitoringPerfTick[]
}

let state: PerfState | undefined
let activeTickId: number | undefined

function perfState(): PerfState {
  if (state !== undefined) return state
  const samples: MonitoringPerfTick[] = []
  const next: PerfState = {
    nextTickId: 0,
    tickCount: 0,
    lastTickAt: null,
    requestCount: 0,
    latestCompletedRequest: null,
    lastResizeMs: null,
    resizeCount: 0,
    pending: new Map(),
    samples,
  }
  const debug: MonitoringPerfDebug = {
    get tickCount() {
      return next.tickCount
    },
    get lastTickAt() {
      return next.lastTickAt
    },
    get samples() {
      return samples
    },
    get requestCount() {
      return next.requestCount
    },
    get lastResizeMs() {
      return next.lastResizeMs
    },
    get resizeCount() {
      return next.resizeCount
    },
  }
  Object.defineProperty(window, '__monitoringPerf', {
    configurable: true,
    enumerable: false,
    value: debug,
  })
  state = next
  return next
}

function pendingTick(): PendingTick | undefined {
  return activeTickId === undefined ? undefined : perfState().pending.get(activeTickId)
}

function duration<T>(name: string, callback: () => T): { readonly result: T; readonly measured: number } {
  const start = `${name}:start`
  const end = `${name}:end`
  performance.mark(start)
  const result = callback()
  performance.mark(end)
  performance.measure(name, start, end)
  const measured = performance.getEntriesByName(name).at(-1)?.duration ?? 0
  performance.clearMarks(start)
  performance.clearMarks(end)
  performance.clearMeasures(name)
  return { result, measured }
}

export function beginMonitoringPerfTick(): number {
  const current = perfState()
  const id = current.nextTickId + 1
  current.nextTickId = id
  const requestStartedAt = current.latestCompletedRequest?.startedAt ?? null
  current.pending.set(id, {
    id,
    startedAt: performance.now(),
    requestStartedAt,
    alignMs: 0,
    convertMs: 0,
    setDataMs: 0,
  })
  activeTickId = id
  performance.mark(`monitoring:tick:${id}:start`)
  return id
}

export function measureMonitoringAlignment<T>(callback: () => T): T {
  const { result, measured } = duration('monitoring:align', callback)
  const pending = pendingTick()
  if (pending !== undefined) pending.alignMs += measured
  return result
}

export function measureMonitoringConversion<T>(callback: () => T): T {
  const { result, measured } = duration('monitoring:convert', callback)
  const pending = pendingTick()
  if (pending !== undefined) pending.convertMs += measured
  return result
}

export function measureMonitoringSetData(callback: () => void): void {
  const { measured } = duration('monitoring:set-data', callback)
  const pending = pendingTick()
  if (pending !== undefined) pending.setDataMs += measured
}

export function measureMonitoringResize(callback: () => void): void {
  const { measured } = duration('monitoring:resize', callback)
  const current = perfState()
  current.lastResizeMs = measured
  current.resizeCount += 1
}

export function finishMonitoringPerfTick(sourceTimestamp: number | undefined): void {
  const tickId = activeTickId
  if (tickId === undefined) return
  requestAnimationFrame(() => {
    const current = perfState()
    const pending = current.pending.get(tickId)
    if (pending === undefined) return
    const paintAt = performance.now()
    const start = `monitoring:tick:${tickId}:start`
    const paint = `monitoring:tick:${tickId}:paint`
    performance.mark(paint)
    performance.measure(`monitoring:tick:${tickId}:commit`, start, paint)
    const commitMs = performance.getEntriesByName(`monitoring:tick:${tickId}:commit`).at(-1)?.duration ?? 0
    const observedAt = sourceTimestamp ?? performance.timeOrigin + paintAt
    current.samples.push({
      tickIndex: tickId,
      alignMs: pending.alignMs,
      convertMs: pending.convertMs,
      setDataMs: pending.setDataMs,
      totalTickMs: pending.alignMs + pending.convertMs + pending.setDataMs,
      commitMs,
      requestToPaintMs:
        pending.requestStartedAt === null ? null : paintAt - pending.requestStartedAt,
      visualAgeMs: performance.timeOrigin + paintAt - observedAt,
    })
    current.tickCount += 1
    current.lastTickAt = paintAt
    current.pending.delete(tickId)
    performance.clearMarks(start)
    performance.clearMarks(paint)
    performance.clearMeasures(`monitoring:tick:${tickId}:commit`)
  })
}

export function startMonitoringRequest(): number {
  const current = perfState()
  current.requestCount += 1
  return performance.now()
}

export function finishMonitoringRequest(startedAt: number): void {
  perfState().latestCompletedRequest = { startedAt, finishedAt: performance.now() }
}

export function injectMonitoringAlignmentDelay(): void {
  if (!window.location.search.includes('scenario=performance-delay') || ALIGNMENT_DELAY_MS <= 0) return
  const until = performance.now() + ALIGNMENT_DELAY_MS
  while (performance.now() < until) {
    continue
  }
}
