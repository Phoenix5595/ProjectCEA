import { sensorUrlClustersFor } from '../../../config/clusterTopology'
import { logger } from '../../../utils/logger'
import { MonitoringApi } from '../api'

import {
  applyControl,
  applyControlFresh,
  applyProjection,
  lastControlTimestamp,
  projectionExpired,
} from './monitoringStore.control'
import { downgradeQuality, iso } from './monitoringStore.merge'
import { applySensorIncremental, applySensorRange } from './monitoringStore.sensor'
import type { LiveRequest, PollerHooks } from './monitoringStore.poller.types'
import {
  controlTailStart,
  isSourceRetryEligible,
  LIVE_SENSOR_FULL_RELOAD_MS,
  nextSensorIncrementalAt,
  pollingEligibility,
  sensorIncrementalStart,
} from './monitoringStore.pollingPolicy'

export type { LiveRequest, PollerHooks } from './monitoringStore.poller.types'

/** Retry a live full sensor load when the base has no completed bucket yet. */
const EMPTY_BASE_RETRY_MS = 60_000

export class MonitoringLivePoller {
  private controlWindowInFlight = false
  private projectionInFlight = false
  private sensorHistoryInFlight = false
  private readonly liveInFlight = new Set<string>()
  private lastReconcileAt: Date | null = null
  private lastProjectionAttemptAt: Date | null = null
  private lastSensorHistoryRefreshAt: number
  private lastSensorFullRefreshAt: number
  private sensorHistoryFailed = false
  private reconciling = false
  private controlReconcilePending = false

  constructor(
    private readonly location: string,
    private readonly monitoringApi: MonitoringApi,
    private readonly hooks: PollerHooks
  ) {
    this.lastSensorHistoryRefreshAt = hooks.now().getTime()
    this.lastSensorFullRefreshAt = this.lastSensorHistoryRefreshAt
  }

  /** Reset the incremental schedule after the range loader installed a base. */
  markFullRangeLoad(at: Date): void {
    this.lastSensorHistoryRefreshAt = at.getTime()
    this.lastSensorFullRefreshAt = at.getTime()
    this.sensorHistoryFailed = false
  }

  tick(): void {
    if (this.hooks.isPaused() || !this.hooks.isActive()) return
    const mode = this.hooks.isHistorical() ? 'historical' : 'monitoring'
    if (mode === 'monitoring') void this.pollSensors()
    // Recorded reads wait for the first chart-reported budget (R1).
    if (this.hooks.getRangeMaxPoints() === null) return
    const eligibility = pollingEligibility(this.hooks.read().range, mode)
    if (eligibility.projection) this.checkProjection()
    if (eligibility.sensorHistory) void this.refreshSensorHistory()
    if (eligibility.controlTail) void this.pollControlWindow()
  }

  private checkProjection(): void {
    const state = this.hooks.read()
    const data = state.data
    const now = this.hooks.now()
    const outcome = state.sourceOutcomes.projection
    const failed = outcome?.status === 'failed'
    if (!projectionExpired(data, now) && !failed) return
    const lastAttemptAt =
      this.lastProjectionAttemptAt ?? (outcome?.status === 'failed' ? outcome.errorAt : null)
    if (!isSourceRetryEligible('projection', now, lastAttemptAt)) return
    const request = this.hooks.liveRequest()
    if (request === null) return
    const current = data.anchorQuality ?? 'exact'
    const downgraded = downgradeQuality(current)
    if (downgraded !== data.anchorQuality) {
      this.hooks.applyData({ ...data, anchorQuality: downgraded })
    }
    void this.reloadProjection(request)
  }

  private async reloadProjection(request: LiveRequest): Promise<void> {
    if (this.projectionInFlight || !this.hooks.isActive()) return
    this.projectionInFlight = true
    try {
      const resp = await this.monitoringApi.controlProjection(this.location)
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const { data, changed } = applyProjection(this.hooks.read().data, resp)
      if (changed) this.hooks.applyData(data)
      this.hooks.applySourceSuccess('projection', this.hooks.now())
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.hooks.applySourceFailure({
        source: 'projection',
        message: err instanceof Error ? err.message : String(err),
        errorAt: this.hooks.now(),
      })
      logger.warn('projection reload failed', err)
    } finally {
      if (this.hooks.isLiveRequestCurrent(request)) this.lastProjectionAttemptAt = this.hooks.now()
      this.projectionInFlight = false
    }
  }

  private rangeBounds(): { start: Date; end: Date } {
    const r = this.hooks.read().range
    if (r.kind === 'fixed') return { start: r.start, end: r.end }
    const now = this.hooks.now()
    return { start: new Date(now.getTime() - r.duration), end: now }
  }

  private async pollSensors(): Promise<void> {
    const nodes = sensorUrlClustersFor(this.location)
    for (const node of nodes) {
      if (this.liveInFlight.has(node)) continue
      this.liveInFlight.add(node)
      void this.monitoringApi
        .sensorLive(this.location, node)
        .then(values => {
          if (!this.hooks.isActive() || this.hooks.isPaused()) return
          this.hooks.applyLiveValues(values)
        })
        .catch((err: unknown) => {
          if (this.hooks.isActive() && !this.hooks.isPaused())
            logger.warn('live sensor poll failed', err)
        })
        .finally(() => this.liveInFlight.delete(node))
    }
  }

  /**
   * Live sensor refresh: completed buckets only, appended incrementally from
   * an overlapped start, with a periodic full reload for statistics.
   */
  private async refreshSensorHistory(): Promise<void> {
    const state = this.hooks.read()
    if (state.range.kind !== 'live' || this.sensorHistoryInFlight) return
    const requestedRange = state.range
    const request = this.hooks.liveRequest()
    if (request === null) return
    const now = this.hooks.now()
    const nowMs = now.getTime()
    if (
      this.sensorHistoryFailed &&
      !isSourceRetryEligible('sensor-history', now, new Date(this.lastSensorHistoryRefreshAt))
    ) {
      return
    }
    const baseCompleteUntil = state.data.liveTail.baseCompleteUntil
    if (baseCompleteUntil === null) {
      if (!this.sensorHistoryFailed && nowMs - this.lastSensorHistoryRefreshAt < EMPTY_BASE_RETRY_MS) {
        return
      }
      await this.fullSensorRefresh(request, requestedRange, now)
      return
    }
    if (nowMs - this.lastSensorFullRefreshAt >= LIVE_SENSOR_FULL_RELOAD_MS) {
      await this.fullSensorRefresh(request, requestedRange, now)
      return
    }
    const intervalMs = state.data.liveTail.intervalMs
    if (nowMs < nextSensorIncrementalAt(this.lastSensorHistoryRefreshAt, baseCompleteUntil, intervalMs)) {
      return
    }
    // The backend rejects an explicit interval that would exceed the budget;
    // a stalled baseline widens the window, so fall back to a full load then.
    const budget = this.hooks.getRangeMaxPoints()
    const incrementalPoints = Math.ceil(
      (nowMs - sensorIncrementalStart(baseCompleteUntil, intervalMs)) / intervalMs
    )
    if (budget !== null && incrementalPoints > budget) {
      await this.fullSensorRefresh(request, requestedRange, now)
      return
    }
    await this.incrementalSensorRefresh(request, requestedRange, now)
  }

  private async fullSensorRefresh(
    request: LiveRequest,
    range: { readonly duration: number },
    requestedAt: Date
  ): Promise<void> {
    const maxPoints = this.hooks.getRangeMaxPoints()
    if (maxPoints === null) return
    this.sensorHistoryInFlight = true
    this.lastSensorHistoryRefreshAt = requestedAt.getTime()
    const start = new Date(requestedAt.getTime() - range.duration)
    try {
      const response = await this.monitoringApi.sensorRange(
        this.location,
        iso(start),
        iso(requestedAt),
        maxPoints
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const completedAt = this.hooks.now()
      this.hooks.applyData(
        applySensorRange(this.hooks.read().data, response, {
          live: true,
          nowMs: completedAt.getTime(),
          durationMs: range.duration,
        })
      )
      this.hooks.applySourceSuccess('sensor-history', completedAt)
      this.lastSensorFullRefreshAt = completedAt.getTime()
      this.sensorHistoryFailed = false
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.sensorHistoryFailed = true
      this.hooks.applySourceFailure({
        source: 'sensor-history',
        message: err instanceof Error ? err.message : String(err),
        errorAt: this.hooks.now(),
      })
      logger.warn('sensor history refresh failed', err)
    } finally {
      this.sensorHistoryInFlight = false
    }
  }

  private async incrementalSensorRefresh(
    request: LiveRequest,
    range: { readonly duration: number },
    requestedAt: Date
  ): Promise<void> {
    const data = this.hooks.read().data
    const baseline = data.liveTail.baseCompleteUntil
    if (baseline === null) return
    const maxPoints = this.hooks.getRangeMaxPoints()
    if (maxPoints === null) return
    const intervalMs = data.liveTail.intervalMs
    const intervalSeconds = data.sensorIntervalSeconds ?? Math.max(1, Math.round(intervalMs / 1_000))
    this.sensorHistoryInFlight = true
    this.lastSensorHistoryRefreshAt = requestedAt.getTime()
    try {
      const response = await this.monitoringApi.sensorRange(
        this.location,
        iso(new Date(sensorIncrementalStart(baseline, intervalMs))),
        iso(requestedAt),
        maxPoints,
        { intervalSeconds }
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const completedAt = this.hooks.now()
      const current = this.hooks.read().data
      const next = applySensorIncremental(current, response, {
        live: true,
        nowMs: completedAt.getTime(),
        durationMs: range.duration,
      })
      if (next !== current) this.hooks.applyData(next)
      this.hooks.applySourceSuccess('sensor-history', completedAt)
      this.sensorHistoryFailed = false
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.sensorHistoryFailed = true
      this.hooks.applySourceFailure({
        source: 'sensor-history',
        message: err instanceof Error ? err.message : String(err),
        errorAt: this.hooks.now(),
      })
      logger.warn('sensor incremental refresh failed', err)
    } finally {
      this.sensorHistoryInFlight = false
    }
  }

  private async pollControlWindow(): Promise<void> {
    if (
      this.controlWindowInFlight ||
      this.reconciling ||
      this.hooks.isPaused() ||
      !this.hooks.isActive()
    ) {
      return
    }
    const request = this.hooks.liveRequest()
    if (request === null) return
    const maxPoints = this.hooks.getRangeMaxPoints()
    if (maxPoints === null) return
    this.controlWindowInFlight = true
    this.hooks.setFlags({ tailLoading: true })
    try {
      const now = this.hooks.now()
      const last = lastControlTimestamp(this.hooks.read().data)
      const start = controlTailStart(now, last)
      const resp = await this.monitoringApi.controlTail(
        this.location,
        iso(start),
        iso(now),
        maxPoints
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const previous = this.hooks.read()
      if (previous.data.controlHistory) {
        this.hooks.applyData(applyControl(previous.data, resp))
      } else {
        this.hooks.applyData(applyControlFresh(previous.data, resp))
      }
      // A healthy tail refreshes control freshness. After a failure the tail
      // alone cannot prove the window is complete: the next full read decides
      // (it clears the error at once when the outage has ended, while a
      // persistent history outage stays visible).
      const wasFailed = previous.sourceOutcomes['control-history'].status === 'failed'
      if (!wasFailed && !this.controlReconcilePending) {
        this.hooks.applySourceSuccess('control-history', this.hooks.now())
      }
      if (wasFailed) this.controlReconcilePending = true
      if (this.controlReconcilePending) await this.reconcile(request)
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
      logger.warn('control window failed, reconciling', err)
      this.hooks.applySourceFailure({
        source: 'control-history',
        message: err instanceof Error ? err.message : String(err),
        errorAt: this.hooks.now(),
      })
      await this.reconcile(request)
    } finally {
      this.controlWindowInFlight = false
      this.hooks.setFlags({ tailLoading: false })
    }
  }

  private async reconcile(request: LiveRequest): Promise<void> {
    const now = this.hooks.now()
    // The first reconcile runs immediately; later attempts follow the cadence.
    const lastAttemptAt = this.lastReconcileAt
    if (
      !this.hooks.isLiveRequestCurrent(request) ||
      this.reconciling ||
      !isSourceRetryEligible('control-history', now, lastAttemptAt)
    )
      return
    this.lastReconcileAt = now
    if (!this.hooks.isActive()) return
    const maxPoints = this.hooks.getRangeMaxPoints()
    if (maxPoints === null) return
    this.reconciling = true
    this.hooks.setFlags({ reconciling: true })
    try {
      const { start, end } = this.rangeBounds()
      const resp = await this.monitoringApi.controlRange(
        this.location,
        iso(start),
        iso(end),
        maxPoints
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.hooks.applyData(applyControlFresh(this.hooks.read().data, resp))
      this.hooks.applySourceSuccess('control-history', this.hooks.now())
      this.controlReconcilePending = false
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.hooks.applySourceFailure({
        source: 'control-history',
        message: err instanceof Error ? err.message : String(err),
        errorAt: this.hooks.now(),
      })
      logger.warn('reconciliation failed', err)
    } finally {
      this.reconciling = false
      this.hooks.setFlags({ reconciling: false })
    }
  }
}
