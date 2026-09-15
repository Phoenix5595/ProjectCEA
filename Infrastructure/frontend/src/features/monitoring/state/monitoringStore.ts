import {
  CONTROL_HISTORY_MAX_POINTS,
  MonitoringApi,
  SENSOR_RANGE_MAX_POINTS,
} from '../api'
import type { MonitoringRange, MonitoringStoreOptions, StoreState } from './monitoringStore.types'

import { applyInitialPartial } from './monitoringStore.control'
import { createIdleSourceOutcomes, deriveActiveSourceErrors, deriveRangeFreshness } from './monitoringStore.health'
import { iso, sameRange } from './monitoringStore.merge'
import {
  applySettledSource,
  applySourceFailureToState,
  applySourceSuccessToState,
} from './monitoringStore.outcomes'
import { MonitoringLivePoller } from './monitoringStore.poller'
import { rangeBounds, rangeChanged } from './monitoringStore.range'

const DEFAULT_POLL_MS = 1000
const DEFAULT_DURATION_MS = 3600_000

interface RangeBudget {
  readonly sensor: number
  readonly control: number
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
      liveRequest: () => this.state.range.kind === 'live'
        ? { range: this.state.range, generation: this.rangeSequence }
        : null,
      isLiveRequestCurrent: (request) =>
        this.active &&
        !this.paused &&
        this.rangeSequence === request.generation &&
        sameRange(this.state.range, request.range),
      applySourceSuccess: (source, lastGoodAt) => {
        this.setState(applySourceSuccessToState(this.state, { source, lastGoodAt }))
      },
      applySourceFailure: (failure) => {
        this.setState(applySourceFailureToState(this.state, failure))
      },
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

  setRangeBudget(maxPoints: number): void {
    if (this.rangeBudget.sensor === maxPoints && this.rangeBudget.control === maxPoints) return
    this.rangeBudget = { sensor: maxPoints, control: maxPoints }
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
      fulfilledRange: null,
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
        projectionVersion: null,
        anchorFingerprint: null,
        anchorQuality: null,
        anchorValidUntil: null,
        runtimeSnapshotVersion: null,
        flushHealth: [],
      },
      sourceOutcomes: createIdleSourceOutcomes(),
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
    if (!options.force && !rangeChanged(this.state.range, this.lastRangeIdentity)) return
    const sequence = ++this.rangeSequence
    const requestedRange = this.state.range
    const { start: requestedStart, end: requestedEnd } = rangeBounds(this.state.range, this.now())
    const controller = new AbortController()
    this.rangeController = controller
    this.rangeInFlight = true
    this.setState({ loading: true })
    const settled = await Promise.allSettled([
      this.monitoringApi.sensorRange(this.location, iso(requestedStart), iso(requestedEnd), this.rangeBudget.sensor, {
        signal: controller.signal,
      }),
      this.monitoringApi.controlRange(this.location, iso(requestedStart), iso(requestedEnd), this.rangeBudget.control, {
        signal: controller.signal,
      }),
      this.monitoringApi.controlProjection(this.location, {
        signal: controller.signal,
      }),
    ])
    if (this.rangeController === controller) {
      this.rangeInFlight = false
      this.rangeController = null
    }
    if (!this.active) return
    if (sequence !== this.rangeSequence) return
    const completedAt = this.now()
    const sensorResult = applySettledSource(this.state, 'sensor-history', settled[0], completedAt)
    const controlResult = applySettledSource(sensorResult.state, 'control-history', settled[1], completedAt)
    const projectionResult = applySettledSource(controlResult.state, 'projection', settled[2], completedAt)
    const sensorRange = sensorResult.value
    const controlRange = controlResult.value
    const projection = projectionResult.value
    const outcomes = projectionResult.state.sourceOutcomes
    const errors = deriveActiveSourceErrors(outcomes).map(({ message }) => message)
    this.lastRangeIdentity = requestedRange
    const hasHistory = sensorRange !== null || controlRange !== null
    const rangeFreshness = deriveRangeFreshness(outcomes)
    this.setState({
      loading: false,
      errors,
      sourceOutcomes: outcomes,
      data: hasHistory || projection !== null
        ? applyInitialPartial(this.state.data, sensorRange, controlRange, projection)
        : this.state.data,
      ...(hasHistory
        ? {
            fulfilledRange: {
              range: requestedRange,
              start: requestedStart,
              end: requestedEnd,
              revision: (this.state.fulfilledRange?.revision ?? 0) + 1,
            },
          }
        : {}),
      ...rangeFreshness,
    })
  }
}
