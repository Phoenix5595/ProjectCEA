import {
  CONTROL_HISTORY_MAX_POINTS,
  MonitoringApi,
  MonitoringAbortError,
  SENSOR_RANGE_MAX_POINTS,
} from '../api'
import type { MonitoringRange, MonitoringStoreOptions, StoreState } from './monitoringStore.types'

import { applyInitialPartial } from './monitoringStore.control'
import { iso, sameRange } from './monitoringStore.merge'
import { MonitoringLivePoller } from './monitoringStore.poller'

const DEFAULT_POLL_MS = 1000
const DEFAULT_DURATION_MS = 3600_000

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

interface RangeBounds {
  start: Date
  end: Date
}

interface RangeBudget {
  readonly sensor: number
  readonly control: number
}

function rangeResult<T>(result: PromiseSettledResult<T>, errors: string[]): T | null {
  if (result.status === 'fulfilled') return result.value
  if (!(result.reason instanceof MonitoringAbortError)) errors.push(errorMessage(result.reason))
  return null
}

export class MonitoringStore {
  private state: StoreState
  private readonly listeners = new Set<() => void>()
  private readonly pollIntervalMs: number
  private readonly now: () => Date
  private readonly poller: MonitoringLivePoller
  private timerId: ReturnType<typeof setInterval> | null = null
  private subscriberCount = 0
  private active = false
  private paused = false
  private rangeSequence = 0
  private lastRangeIdentity?: MonitoringRange
  private rangeInFlight = false
  private rangeController: AbortController | null = null
  private rangeBudget: RangeBudget = {
    sensor: SENSOR_RANGE_MAX_POINTS,
    control: CONTROL_HISTORY_MAX_POINTS,
  }

  constructor(
    private readonly location: string,
    private readonly monitoringApi: MonitoringApi,
    options: MonitoringStoreOptions = {},
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_MS
    this.now = options.now ?? (() => new Date())
    this.state = this.initialState()
    this.poller = new MonitoringLivePoller(location, monitoringApi, {
      read: () => this.state,
      applyData: (data) => this.setState({ data }),
      setFlags: (patch) => this.setState(patch),
      isActive: () => this.active,
      isPaused: () => this.paused,
      now: () => this.now(),
    })
  }

  getSnapshot(): StoreState {
    return this.state
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    this.subscriberCount += 1
    if (this.subscriberCount === 1) {
      this.active = true
      void this.loadRangeIfChanged()
      this.startTimer()
    }
    return () => {
      this.listeners.delete(listener)
      this.subscriberCount -= 1
      if (this.subscriberCount === 0) {
        this.active = false
        this.stopTimer()
        this.rangeSequence += 1
        this.abortRangeLoad()
      }
    }
  }

  pause(): void {
    this.paused = true
    this.stopTimer()
  }

  resume(): void {
    this.paused = false
    if (this.subscriberCount > 0 && this.timerId === null) {
      this.startTimer()
      void this.tick()
    }
  }

  setFixedRange(start: Date, end: Date): void {
    const nextRange: MonitoringRange = { kind: 'fixed', start, end }
    if (sameRange(this.state.range, nextRange)) return
    this.rangeSequence += 1
    this.abortRangeLoad()
    this.setState({ range: nextRange, isLive: false })
    void this.loadRangeIfChanged()
  }

  setLiveRange(duration: number): void {
    const nextRange: MonitoringRange = { kind: 'live', duration }
    if (sameRange(this.state.range, nextRange)) return
    this.rangeSequence += 1
    this.abortRangeLoad()
    this.setState({ range: nextRange, isLive: true })
    void this.loadRangeIfChanged()
  }

  /** Refresh range sources at the chart-derived resolution. */
  setRangeBudget(maxPoints: number): void {
    if (this.rangeBudget.sensor === maxPoints && this.rangeBudget.control === maxPoints) return
    this.rangeSequence += 1
    this.abortRangeLoad()
    this.rangeBudget = { sensor: maxPoints, control: maxPoints }
    void this.loadRangeIfChanged({ force: true })
  }

  /** Re-run the initial range load, preserving last-good data on failure. */
  retry(): void {
    this.rangeSequence += 1
    this.abortRangeLoad()
    void this.loadRangeIfChanged({ force: true })
  }

  private initialState(): StoreState {
    return {
      range: { kind: 'live', duration: DEFAULT_DURATION_MS },
      isLive: true,
      data: {
        series: [],
        statistics: [],
        live: [],
        controlHistory: null,
        projectionHistory: null,
        photoperiod: [],
        cursors: [],
        projectionRevision: null,
        anchorFingerprint: null,
        anchorQuality: null,
        anchorValidUntil: null,
        runtimeSnapshotVersion: null,
        flushHealth: [],
      },
      loading: true,
      tailLoading: false,
      reconciling: false,
      errors: [],
      lastGoodRangeAt: null,
      rangeErrorAt: null,
    }
  }

  private startTimer(): void {
    if (this.timerId !== null) return
    this.timerId = setInterval(() => {
      void this.tick()
    }, this.pollIntervalMs)
  }

  private stopTimer(): void {
    if (this.timerId !== null) {
      clearInterval(this.timerId)
      this.timerId = null
    }
  }

  private rangeBounds(now: Date): RangeBounds {
    const r = this.state.range
    if (r.kind === 'fixed') return { start: r.start, end: r.end }
    return { start: new Date(now.getTime() - r.duration), end: now }
  }

  private rangeChanged(): boolean {
    return this.lastRangeIdentity === undefined || !sameRange(this.state.range, this.lastRangeIdentity)
  }

  private setState(patch: Partial<StoreState>): void {
    this.state = { ...this.state, ...patch }
    this.emit()
  }

  private emit(): void {
    if (!this.active) return
    for (const l of this.listeners) l()
  }

  private tick(): void {
    void this.loadRangeIfChanged()
    this.poller.tick()
  }

  private abortRangeLoad(): void {
    this.rangeController?.abort()
    this.rangeController = null
    this.rangeInFlight = false
  }

  private async loadRangeIfChanged(options: { force?: boolean } = {}): Promise<void> {
    if (this.rangeInFlight) return
    if (!options.force && !this.rangeChanged()) return
    const sequence = ++this.rangeSequence
    const controller = new AbortController()
    this.rangeController = controller
    this.rangeInFlight = true
    this.setState({ loading: true })
    const { start, end } = this.rangeBounds(this.now())
    const settled = await Promise.allSettled([
      this.monitoringApi.sensorRange(this.location, iso(start), iso(end), this.rangeBudget.sensor, {
        signal: controller.signal,
      }),
      this.monitoringApi.controlRange(this.location, iso(start), iso(end), this.rangeBudget.control, {
        signal: controller.signal,
      }),
      this.monitoringApi.controlProjection(this.location, iso(start), iso(end), {
        signal: controller.signal,
      }),
    ])
    if (this.rangeController === controller) {
      this.rangeInFlight = false
      this.rangeController = null
    }
    if (!this.active) return
    if (sequence !== this.rangeSequence) return
    const errors: string[] = []
    const sensorRange = rangeResult(settled[0], errors)
    const controlRange = rangeResult(settled[1], errors)
    const projection = rangeResult(settled[2], errors)
    this.lastRangeIdentity = this.state.range
    const completedAt = this.now()
    const rangeFreshness = errors.length === 0
      ? { lastGoodRangeAt: completedAt, rangeErrorAt: null }
      : { rangeErrorAt: completedAt }
    this.setState({
      loading: false,
      errors,
      data: applyInitialPartial(this.state.data, sensorRange, controlRange, projection),
      ...rangeFreshness,
    })
  }
}
