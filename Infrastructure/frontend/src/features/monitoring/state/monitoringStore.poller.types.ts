import type { LiveSensorValue } from '../api'

import type { MonitoringRangeSource } from './monitoringStore.health'
import type { MonitoringRange, StoreData, StoreState } from './monitoringStore.types'

export type LiveRequest = {
  readonly range: MonitoringRange
  readonly generation: number
}

export interface PollerHooks {
  read: () => StoreState
  /** Current page point budget, or null before any chart reported a width. */
  getRangeMaxPoints: () => number | null
  applyData: (data: StoreData) => void
  /** Append live values to the raw tail and the latest-value snapshot. */
  applyLiveValues: (values: LiveSensorValue[]) => void
  setFlags: (patch: Partial<StoreState>) => void
  isActive: () => boolean
  isPaused: () => boolean
  /** Historical stores never poll live values, tails or projections. */
  isHistorical: () => boolean
  now: () => Date
  liveRequest: () => LiveRequest | null
  isLiveRequestCurrent: (request: LiveRequest) => boolean
  applySourceSuccess: (source: MonitoringRangeSource, lastGoodAt: Date) => void
  applySourceFailure: (failure: {
    readonly source: MonitoringRangeSource
    readonly message: string
    readonly errorAt: Date
  }) => void
}
