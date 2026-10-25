/**
 * Page-level `MonitoringStore` hook for the monitoring dashboards.
 *
 * Creates one `MonitoringStore` instance per page mount (never in React state),
 * constructed with fresh same-origin sensor/control API clients, and subscribes
 * to it via `useSyncExternalStore`. The store stops polling when the last
 * subscriber unsubscribes (route change / unmount). The caller may set the
 * initial live range before the first subscription so the first load uses it.
 *
 * `mode` selects the page contract: Monitoring stores poll live values and
 * load the projection; Historical stores load recorded ranges only.
 */
import { useCallback, useRef, useSyncExternalStore } from 'react'

import { MonitoringApi } from '../api'
import type { MonitoringRequestContext } from '../api'
import { apiClient } from '../../../services/api'
import { MonitoringStore } from '../state'
import type { MonitoringStoreMode, StoreState } from '../state'

export const FLOWER_DEFAULT_DURATION_MS = 3 * 3600_000
export const VEG_DEFAULT_DURATION_MS = 3600_000

export interface MonitoringStoreBinding {
  readonly snapshot: StoreState
  readonly store: MonitoringStore
  readonly requestContext?: MonitoringRequestContext
}

export function useMonitoringStore(
  location: string,
  requestContext?: MonitoringRequestContext,
  mode: MonitoringStoreMode = 'monitoring',
  defaultLiveDurationMs?: number
): MonitoringStoreBinding {
  const storeRef = useRef<MonitoringStore | null>(null)
  const requestContextRef = useRef(requestContext)
  if (storeRef.current === null) {
    // Room Monitoring/Historical reads use the `room` history scope: recorded
    // climate setpoints + raw lights + photoperiod, with a rejecting contract
    // still validating the envelope. The registry load is retained for the
    // delayed canonical display names of the room lights.
    storeRef.current = new MonitoringStore(
      location,
      new MonitoringApi(requestContextRef.current, undefined, 'room'),
      {
        now: () => new Date(),
        mode,
        defaultLiveDurationMs,
        loadDeviceRegistry: () => apiClient.getDeviceRegistry(),
      }
    )
  }
  const store = storeRef.current
  const subscribe = useCallback((listener: () => void) => store.subscribe(listener), [store])
  const snapshot = useSyncExternalStore(subscribe, () => store.getSnapshot())
  return { snapshot, store, requestContext: requestContextRef.current }
}
