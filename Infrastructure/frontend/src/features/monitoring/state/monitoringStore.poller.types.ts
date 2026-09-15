import type { MonitoringRangeSource } from './monitoringStore.health'
import type { MonitoringRange, StoreData, StoreState } from './monitoringStore.types'

export type LiveRequest = {
  readonly range: MonitoringRange
  readonly generation: number
}

export interface PollerHooks {
  read: () => StoreState
  applyData: (data: StoreData) => void
  setFlags: (patch: Partial<StoreState>) => void
  isActive: () => boolean
  isPaused: () => boolean
  now: () => Date
  liveRequest: () => LiveRequest | null
  isLiveRequestCurrent: (request: LiveRequest) => boolean
  applySourceSuccess: (source: MonitoringRangeSource, lastGoodAt: Date) => void
  applySourceFailure: (failure: { readonly source: MonitoringRangeSource; readonly message: string; readonly errorAt: Date }) => void
}
