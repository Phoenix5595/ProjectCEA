import { sensorUrlClustersFor } from '../../../config/clusterTopology'
import { logger } from '../../../utils/logger'
import { CONTROL_HISTORY_MAX_POINTS, MonitoringApi, SENSOR_RANGE_MAX_POINTS } from '../api'

import {
  applyControl,
  applyControlFresh,
  applyProjection,
  lastControlTimestamp,
  projectionExpired,
} from './monitoringStore.control'
import { downgradeQuality, iso, mergeLive } from './monitoringStore.merge'
import { controlTailStart, isSourceRetryEligible, pollingEligibility } from './monitoringStore.pollingPolicy'
import type { LiveRequest, PollerHooks } from './monitoringStore.poller.types'

export type { LiveRequest, PollerHooks } from './monitoringStore.poller.types'

export class MonitoringLivePoller {
  private controlWindowInFlight = false
  private projectionInFlight = false
  private sensorHistoryInFlight = false
  private readonly liveInFlight = new Set<string>()
  private lastReconcileAt: Date | null = null
  private lastProjectionAttemptAt: Date | null = null
  private controlRecoveryNotBefore: Date | null = null
  private lastSensorHistoryRefreshAt: number
  private reconciling = false

  constructor(
    private readonly location: string,
    private readonly monitoringApi: MonitoringApi,
    private readonly hooks: PollerHooks,
  ) {
    this.lastSensorHistoryRefreshAt = hooks.now().getTime()
  }

  tick(): void {
    if (this.hooks.isPaused() || !this.hooks.isActive()) return
    void this.pollSensors()
    const eligibility = pollingEligibility(this.hooks.read().range)
    if (!eligibility.projection && !eligibility.sensorHistory && !eligibility.controlTail) return
    this.checkProjection()
    void this.refreshSensorHistory()
    void this.pollControlWindow()
  }

  private checkProjection(): void {
    const state = this.hooks.read()
    const data = state.data
    const now = this.hooks.now()
    const outcome = state.sourceOutcomes.projection
    const failed = outcome?.status === 'failed'
    if (!projectionExpired(data, now) && !failed) return
    const lastAttemptAt = this.lastProjectionAttemptAt ?? (outcome?.status === 'failed' ? outcome.errorAt : null)
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
        .then((values) => {
          if (!this.hooks.isActive() || this.hooks.isPaused()) return
          const data = this.hooks.read().data
          this.hooks.applyData({ ...data, live: mergeLive(data.live, values) })
        })
        .catch((err: unknown) => {
          if (this.hooks.isActive() && !this.hooks.isPaused()) logger.warn('live sensor poll failed', err)
        })
        .finally(() => this.liveInFlight.delete(node))
    }
  }

  private async refreshSensorHistory(): Promise<void> {
    const state = this.hooks.read()
    if (
      state.range.kind !== 'live' ||
      this.sensorHistoryInFlight ||
      !isSourceRetryEligible('sensor-history', this.hooks.now(), new Date(this.lastSensorHistoryRefreshAt))
    ) {
      return
    }
    const requestedRange = state.range
    const request = this.hooks.liveRequest()
    if (request === null) return
    const now = this.hooks.now()
    const start = new Date(now.getTime() - requestedRange.duration)
    this.sensorHistoryInFlight = true
    this.lastSensorHistoryRefreshAt = now.getTime()
    try {
      const response = await this.monitoringApi.sensorRange(
        this.location,
        iso(start),
        iso(now),
        SENSOR_RANGE_MAX_POINTS,
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const current = this.hooks.read()
      this.hooks.applyData({
        ...current.data,
        series: response.series,
        statistics: response.statistics,
      })
      this.hooks.applySourceSuccess('sensor-history', this.hooks.now())
    } catch (err) {
      if (!this.hooks.isLiveRequestCurrent(request)) return
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
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      const previous = this.hooks.read()
      if (previous.data.controlHistory) {
        this.hooks.applyData(applyControl(previous.data, resp))
      } else {
        this.hooks.applyData(applyControlFresh(previous.data, resp))
      }
      if (previous.sourceOutcomes['control-history'].status === 'failed') {
        if (previous.data.controlHistory === null && this.controlRecoveryNotBefore === null) {
          this.controlRecoveryNotBefore = new Date(now.getTime() + 60_000)
        }
        await this.reconcile(request)
      }
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
    if (this.controlRecoveryNotBefore !== null && now < this.controlRecoveryNotBefore) return
    const outcome = this.hooks.read().sourceOutcomes['control-history']
    const lastAttemptAt = this.lastReconcileAt ?? (outcome?.status === 'failed' ? outcome.errorAt : null)
    if (
      !this.hooks.isLiveRequestCurrent(request) ||
      this.reconciling ||
      !isSourceRetryEligible('control-history', now, lastAttemptAt)
    ) return
    this.lastReconcileAt = now
    if (!this.hooks.isActive()) return
    this.reconciling = true
    this.hooks.setFlags({ reconciling: true })
    try {
      const { start, end } = this.rangeBounds()
      const resp = await this.monitoringApi.controlRange(
        this.location,
        iso(start),
        iso(end),
        CONTROL_HISTORY_MAX_POINTS,
      )
      if (!this.hooks.isLiveRequestCurrent(request)) return
      this.hooks.applyData(applyControlFresh(this.hooks.read().data, resp))
      this.hooks.applySourceSuccess('control-history', this.hooks.now())
      this.controlRecoveryNotBefore = null
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
