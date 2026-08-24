import { sensorUrlClustersFor } from '../../../config/clusterTopology'
import { logger } from '../../../utils/logger'
import { MonitoringApi } from '../api'

import {
  applyControl,
  applyControlFresh,
  applyProjection,
  flushHealthRecovered,
  lastControlTimestamp,
  projectionExpired,
} from './monitoringStore.control'
import { downgradeQuality, iso, mergeLive } from './monitoringStore.merge'
import type { StoreData, StoreState } from './monitoringStore.types'

const RECONCILE_MIN_INTERVAL_MS = 30_000

export interface PollerHooks {
  read: () => StoreState
  applyData: (data: StoreData) => void
  setFlags: (patch: Partial<StoreState>) => void
  isActive: () => boolean
  isPaused: () => boolean
  now: () => Date
}

const CONTROL_WINDOW_MS = 120_000

export class MonitoringLivePoller {
  private controlWindowInFlight = false
  private projectionInFlight = false
  private readonly liveInFlight = new Set<string>()
  private lastReconcileAt = 0
  private reconciling = false

  constructor(
    private readonly location: string,
    private readonly monitoringApi: MonitoringApi,
    private readonly hooks: PollerHooks,
  ) {}

  tick(): void {
    if (this.hooks.isPaused() || !this.hooks.isActive()) return
    this.checkProjection()
    void this.pollSensors()
    void this.pollControlWindow()
  }

  private checkProjection(): void {
    const data = this.hooks.read().data
    if (!projectionExpired(data, this.hooks.now())) return
    const current = data.anchorQuality ?? 'exact'
    const downgraded = downgradeQuality(current)
    if (downgraded !== data.anchorQuality) {
      this.hooks.applyData({ ...data, anchorQuality: downgraded })
    }
    void this.reloadProjection()
  }

  private async reloadProjection(): Promise<void> {
    if (this.projectionInFlight || !this.hooks.isActive()) return
    this.projectionInFlight = true
    try {
      const { start, end } = this.rangeBounds()
      const resp = await this.monitoringApi.controlProjection(this.location, iso(start), iso(end))
      if (!this.hooks.isActive() || this.hooks.isPaused()) return
      const { data, changed } = applyProjection(this.hooks.read().data, resp)
      if (changed) this.hooks.applyData(data)
    } catch (err) {
      logger.warn('projection reload failed', err)
    } finally {
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
        .catch(() => {})
        .finally(() => this.liveInFlight.delete(node))
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
    this.controlWindowInFlight = true
    this.hooks.setFlags({ tailLoading: true })
    try {
      const now = this.hooks.now()
      const last = lastControlTimestamp(this.hooks.read().data)
      const start = last ? new Date(last.getTime() - 2000) : new Date(now.getTime() - CONTROL_WINDOW_MS)
      const resp = await this.monitoringApi.controlRange(
        this.location,
        iso(start),
        iso(now),
      )
      if (!this.hooks.isActive() || this.hooks.isPaused()) return
      const previous = this.hooks.read()
      if (previous.data.controlHistory) {
        this.hooks.applyData(applyControl(previous.data, resp))
      } else {
        this.hooks.applyData(applyControlFresh(previous.data, resp))
      }
      if (flushHealthRecovered(previous.data, resp)) {
        await this.reconcile()
      }
    } catch (err) {
      if (!this.hooks.isActive() || this.hooks.isPaused()) return
      logger.warn('control window failed, reconciling', err)
      await this.reconcile()
    } finally {
      this.controlWindowInFlight = false
      this.hooks.setFlags({ tailLoading: false })
    }
  }

  private async reconcile(): Promise<void> {
    const nowMs = Date.now()
    if (this.reconciling || nowMs - this.lastReconcileAt < RECONCILE_MIN_INTERVAL_MS) return
    this.lastReconcileAt = nowMs
    if (!this.hooks.isActive()) return
    this.reconciling = true
    this.hooks.setFlags({ reconciling: true })
    try {
      const { start, end } = this.rangeBounds()
      const resp = await this.monitoringApi.controlRange(this.location, iso(start), iso(end))
      if (!this.hooks.isActive() || this.hooks.isPaused()) return
      this.hooks.applyData(applyControlFresh(this.hooks.read().data, resp))
    } catch (err) {
      logger.warn('reconciliation failed', err)
    } finally {
      this.reconciling = false
      this.hooks.setFlags({ reconciling: false })
    }
  }
}
