import { MonitoringApi } from '../api'

import { applyInitialPartial } from './monitoringStore.control'
import { iso, sameRange } from './monitoringStore.merge'
import { MonitoringLivePoller } from './monitoringStore.poller'
import type { MonitoringRange, MonitoringStoreOptions, StoreState } from './monitoringStore.types'

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
    this.rangeSequence += 1
    this.setState({ range: { kind: 'fixed', start, end }, isLive: false })
    void this.loadRangeIfChanged()
  }

  setLiveRange(duration: number): void {
    this.rangeSequence += 1
    this.setState({ range: { kind: 'live', duration }, isLive: true })
    void this.loadRangeIfChanged()
  }

  /** Re-run the initial range load, preserving last-good data on failure. */
  retry(): void {
    this.rangeSequence += 1
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

  private async loadRangeIfChanged(options: { force?: boolean } = {}): Promise<void> {
    if (this.rangeInFlight) return
    if (!options.force && !this.rangeChanged()) return
    const sequence = ++this.rangeSequence
    this.rangeInFlight = true
    this.setState({ loading: true })
    const { start, end } = this.rangeBounds(this.now())
    const settled = await Promise.allSettled([
      this.monitoringApi.sensorRange(this.location, iso(start), iso(end)),
      this.monitoringApi.sensorStats(this.location, iso(start), iso(end)),
      this.monitoringApi.controlRange(this.location, iso(start), iso(end)),
      this.monitoringApi.controlProjection(this.location, iso(start), iso(end)),
    ])
    this.rangeInFlight = false
    if (!this.active) return
    if (sequence !== this.rangeSequence) {
      void this.loadRangeIfChanged()
      return
    }
    const errors: string[] = []
    const sensorRange =
      settled[0].status === 'fulfilled'
        ? settled[0].value
        : (errors.push(errorMessage(settled[0].reason)), null)
    const sensorStats =
      settled[1].status === 'fulfilled'
        ? settled[1].value
        : (errors.push(errorMessage(settled[1].reason)), null)
    const controlRange =
      settled[2].status === 'fulfilled'
        ? settled[2].value
        : (errors.push(errorMessage(settled[2].reason)), null)
    const projection =
      settled[3].status === 'fulfilled'
        ? settled[3].value
        : (errors.push(errorMessage(settled[3].reason)), null)
    this.lastRangeIdentity = this.state.range
    this.setState({
      loading: false,
      errors,
      data: applyInitialPartial(this.state.data, sensorRange, sensorStats, controlRange, projection),
    })
  }
}
