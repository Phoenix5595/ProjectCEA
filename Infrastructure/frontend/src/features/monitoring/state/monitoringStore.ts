import { MonitoringApi } from '../api'
import { LiveTailBuffer } from '../data/liveTail'
import { logger } from '../../../utils/logger'

import { applyInitialPartial } from './monitoringStore.control'
import {
  createIdleSourceOutcomes,
  deriveActiveSourceErrors,
  deriveRangeFreshness,
} from './monitoringStore.health'
import { iso, sameRange } from './monitoringStore.merge'
import {
  applySettledSource,
  applySourceFailureToState,
  applySourceSuccessToState,
} from './monitoringStore.outcomes'
import { MonitoringLivePoller } from './monitoringStore.poller'
import { fixedRangeRetryDelay } from './monitoringStore.pollingPolicy'
import { rangeBounds, rangeChanged } from './monitoringStore.range'
import { applySensorRange, applyLiveValues } from './monitoringStore.sensor'
import type {
  MonitoringRange,
  MonitoringStoreMode,
  MonitoringStoreOptions,
  StoreState,
} from './monitoringStore.types'

const DEFAULT_POLL_MS = 1_000
const DEFAULT_DURATION_MS = 3_600_000
/** A budget change smaller than this does not justify a reload. */
const BUDGET_RELOAD_THRESHOLD = 0.1
const BUDGET_RELOAD_DEBOUNCE_MS = 500

export class MonitoringStore {
  private state: StoreState
  private readonly listeners = new Set<() => void>()
  private readonly pollIntervalMs: number
  private readonly now: () => Date
  private readonly loadDeviceRegistry: MonitoringStoreOptions['loadDeviceRegistry']
  private readonly mode: MonitoringStoreMode
  private activationGeneration = 0
  private registryLoadingGeneration: number | null = null
  private registryLoadedGeneration: number | null = null
  private registryLoadFailed = false
  private readonly poller: MonitoringLivePoller
  private timerId: ReturnType<typeof setInterval> | null = null
  private budgetTimer: ReturnType<typeof setTimeout> | null = null
  private subscriberCount = 0
  private active = false
  private paused = false
  private rangeSequence = 0
  private lastRangeIdentity?: MonitoringRange
  private rangeInFlight = false
  private rangeController: AbortController | null = null
  /** Page budget; the first range load waits until a chart reports one. */
  private rangeBudget: number | null = null
  private lastLiveDuration = DEFAULT_DURATION_MS
  private fixedRetry: { failures: number; nextAt: number } | null = null
  private budgetReloadPending = false

  constructor(
    private readonly location: string,
    private readonly monitoringApi: MonitoringApi,
    options: MonitoringStoreOptions = {}
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_MS
    this.now = options.now ?? (() => new Date())
    this.loadDeviceRegistry = options.loadDeviceRegistry
    this.mode = options.mode ?? 'monitoring'
    this.lastLiveDuration = options.defaultLiveDurationMs ?? DEFAULT_DURATION_MS
    this.state = this.initialState()
    this.poller = new MonitoringLivePoller(location, monitoringApi, {
      read: () => this.state,
      getRangeMaxPoints: () => this.rangeBudget,
      applyData: data => this.setState({ data }),
      applyLiveValues: values => {
        this.setState({ data: applyLiveValues(this.state.data, values) })
      },
      setFlags: patch => this.setState(patch),
      isActive: () => this.active,
      isPaused: () => this.paused,
      isHistorical: () => this.mode === 'historical',
      now: () => this.now(),
      liveRequest: () =>
        this.state.range.kind === 'live'
          ? { range: this.state.range, generation: this.rangeSequence }
          : null,
      isLiveRequestCurrent: request =>
        this.active &&
        !this.paused &&
        this.rangeSequence === request.generation &&
        sameRange(this.state.range, request.range),
      applySourceSuccess: (source, lastGoodAt) => {
        this.setState(applySourceSuccessToState(this.state, { source, lastGoodAt }))
      },
      applySourceFailure: failure => {
        this.setState(applySourceFailureToState(this.state, failure))
      },
    })
  }

  getSnapshot(): StoreState {
    return this.state
  }

  /** Duration of the most recent live range; the Now button restores it. */
  lastLiveDurationMs(): number {
    return this.lastLiveDuration
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    this.subscriberCount += 1
    if (this.subscriberCount === 1) {
      this.active = true
      const generation = ++this.activationGeneration
      this.loadDeviceRegistryForActivation(generation)
      void this.loadRangeIfChanged()
      this.startTimer()
    }
    return () => {
      this.listeners.delete(listener)
      this.subscriberCount -= 1
      if (this.subscriberCount === 0) {
        this.active = false
        this.activationGeneration += 1
        this.stopTimer()
        this.rangeSequence += 1
        this.abortRangeLoad()
      }
    }
  }

  pause(): void {
    this.paused = true
    this.stopTimer()
    this.setState({ pausedAt: this.now() })
  }

  resume(): void {
    this.paused = false
    this.setState({ pausedAt: null })
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
    this.fixedRetry = null
    this.setState({ range: nextRange, isLive: false })
    void this.loadRangeIfChanged()
  }

  setLiveRange(duration: number): void {
    const nextRange: MonitoringRange = { kind: 'live', duration }
    this.lastLiveDuration = duration
    if (sameRange(this.state.range, nextRange)) return
    this.rangeSequence += 1
    this.abortRangeLoad()
    this.fixedRetry = null
    this.setState({ range: nextRange, isLive: true })
    void this.loadRangeIfChanged()
  }

  /**
   * Install the page budget (minimum plot width over mounted charts). The
   * first call starts the first range load; later calls reload when the
   * budget moved by more than 10 %, debounced to settle resize bursts.
   */
  setRangeBudget(maxPoints: number): void {
    if (!Number.isFinite(maxPoints) || maxPoints <= 0) return
    const next = Math.max(1, Math.floor(maxPoints))
    const previous = this.rangeBudget
    this.rangeBudget = next
    if (previous === null) {
      if (this.active) void this.loadRangeIfChanged()
      return
    }
    if (previous === next) return
    if (Math.abs(next - previous) / previous <= BUDGET_RELOAD_THRESHOLD) return
    this.scheduleBudgetReload()
  }

  /** Re-run the initial range load, preserving last-good data on failure. */
  retry(): void {
    this.rangeSequence += 1
    this.abortRangeLoad()
    this.fixedRetry = null
    void this.loadRangeIfChanged({ force: true })
    if (this.active && this.registryLoadFailed) {
      this.loadDeviceRegistryForActivation(this.activationGeneration)
    }
  }

  private scheduleBudgetReload(): void {
    clearTimeout(this.budgetTimer ?? undefined)
    this.budgetTimer = setTimeout(() => {
      this.budgetTimer = null
      if (!this.active) return
      // A paused view makes no requests; the reload runs on the resume tick.
      if (this.rangeInFlight || this.paused) {
        this.budgetReloadPending = true
        return
      }
      void this.loadRangeIfChanged({ force: true })
    }, BUDGET_RELOAD_DEBOUNCE_MS)
  }

  private loadDeviceRegistryForActivation(generation: number): void {
    const load = this.loadDeviceRegistry
    if (
      load === undefined ||
      !this.active ||
      this.activationGeneration !== generation ||
      this.registryLoadingGeneration === generation ||
      this.registryLoadedGeneration === generation
    ) {
      return
    }
    this.registryLoadFailed = false
    this.registryLoadingGeneration = generation
    void Promise.resolve()
      .then(() =>
        this.active && this.activationGeneration === generation ? load() : null
      )
      .then(entries => {
        if (
          entries === null ||
          !this.active ||
          this.activationGeneration !== generation
        ) {
          return
        }
        const lightRegistry = entries
          .filter(
            entry => entry.location === this.location && entry.device_type === 'light'
          )
          .map(({ device_name, display_name, per_room_index }) => ({
            device_name,
            display_name,
            per_room_index,
          }))
        this.registryLoadedGeneration = generation
        this.setState({ data: { ...this.state.data, lightRegistry } })
      })
      .catch(error => {
        if (!this.active || this.activationGeneration !== generation) return
        this.registryLoadFailed = true
        logger.warn('device registry load failed', error)
      })
      .finally(() => {
        if (this.registryLoadingGeneration === generation) {
          this.registryLoadingGeneration = null
        }
      })
  }

  private initialState(): StoreState {
    return {
      range: { kind: 'live', duration: this.lastLiveDuration },
      fulfilledRange: null,
      isLive: true,
      data: {
        series: [],
        statistics: [],
        live: [],
        liveTail: new LiveTailBuffer(),
        sensorIntervalSeconds: null,
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
      pausedAt: null,
    }
  }

  private startTimer(): void {
    if (this.timerId !== null) return
    this.timerId = setInterval(() => {
      void this.tick()
    }, this.pollIntervalMs)
  }

  private stopTimer(): void {
    clearInterval(this.timerId ?? undefined)
    this.timerId = null
    clearTimeout(this.budgetTimer ?? undefined)
    this.budgetTimer = null
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
    const retry = this.fixedRetry
    if (retry !== null && this.now().getTime() >= retry.nextAt) {
      void this.loadRangeIfChanged({ force: true })
    }
  }

  private abortRangeLoad(): void {
    this.rangeController?.abort()
    this.rangeController = null
    this.rangeInFlight = false
  }

  private async loadRangeIfChanged(options: { force?: boolean } = {}): Promise<void> {
    if (this.rangeInFlight) return
    if (this.rangeBudget === null) return
    const pendingReload = this.budgetReloadPending
    if (!pendingReload && !options.force && !rangeChanged(this.state.range, this.lastRangeIdentity)) {
      return
    }
    this.budgetReloadPending = false
    const sequence = ++this.rangeSequence
    const requestedRange = this.state.range
    const budget = this.rangeBudget
    const { start: requestedStart, end: requestedEnd } = rangeBounds(this.state.range, this.now())
    const controller = new AbortController()
    this.rangeController = controller
    this.rangeInFlight = true
    this.setState({ loading: true })
    const projectionRequest =
      this.mode === 'historical'
        ? Promise.resolve(null)
        : this.monitoringApi.controlProjection(this.location, {
            signal: controller.signal,
          })
    const settled = await Promise.allSettled([
      this.monitoringApi.sensorRange(
        this.location,
        iso(requestedStart),
        iso(requestedEnd),
        budget,
        {
          signal: controller.signal,
        }
      ),
      this.monitoringApi.controlRange(
        this.location,
        iso(requestedStart),
        iso(requestedEnd),
        budget,
        undefined,
        {
          signal: controller.signal,
        }
      ),
      projectionRequest,
    ])
    if (this.rangeController === controller) {
      this.rangeInFlight = false
      this.rangeController = null
    }
    if (!this.active) return
    if (sequence !== this.rangeSequence) return
    const completedAt = this.now()
    const sensorResult = applySettledSource(this.state, 'sensor-history', settled[0], completedAt)
    const controlResult = applySettledSource(
      sensorResult.state,
      'control-history',
      settled[1],
      completedAt
    )
    const projectionResult =
      this.mode === 'historical'
        ? { state: controlResult.state, value: null }
        : applySettledSource(controlResult.state, 'projection', settled[2], completedAt)
    const sensorRange = sensorResult.value
    const controlRange = controlResult.value
    const projection = projectionResult.value
    const outcomes = projectionResult.state.sourceOutcomes
    const errors = deriveActiveSourceErrors(outcomes).map(({ message }) => message)
    this.lastRangeIdentity = requestedRange
    const hasHistory = sensorRange !== null || controlRange !== null
    const rangeFreshness = deriveRangeFreshness(outcomes)
    let data = projectionResult.state.data
    if (sensorRange !== null) {
      data = applySensorRange(data, sensorRange, {
        live: requestedRange.kind === 'live',
        nowMs: completedAt.getTime(),
        durationMs: requestedRange.kind === 'live' ? requestedRange.duration : 0,
      })
      if (requestedRange.kind === 'live') this.poller.markFullRangeLoad(completedAt)
    }
    if (hasHistory || projection !== null) {
      data = applyInitialPartial(data, controlRange, projection)
    }
    this.fixedRetry =
      requestedRange.kind === 'fixed' &&
      (outcomes['sensor-history'].status === 'failed' ||
        outcomes['control-history'].status === 'failed')
        ? {
            failures: (this.fixedRetry?.failures ?? 0) + 1,
            nextAt:
              completedAt.getTime() +
              fixedRangeRetryDelay((this.fixedRetry?.failures ?? 0) + 1),
          }
        : null
    this.setState({
      loading: false,
      errors,
      sourceOutcomes: outcomes,
      data,
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
    if (this.budgetReloadPending && this.active) {
      this.budgetReloadPending = false
      void this.loadRangeIfChanged({ force: true })
    }
  }
}
