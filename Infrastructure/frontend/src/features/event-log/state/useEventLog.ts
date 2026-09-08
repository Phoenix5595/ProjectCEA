import { useEffect, useState, useCallback, useRef, useSyncExternalStore } from 'react'
import { globalEventLogStore, type EventLogEntry, type EventLogPaging } from './eventLogStore'
import {
  addConnectionRef,
  removeConnectionRef,
  getActiveConnections,
  loadOlder,
  resetTransportState,
} from './eventLogTransport'

interface UseEventLogOptions {
  location?: string
  cluster?: string
}

interface UseEventLogResult {
  entries: readonly EventLogEntry[]
  connected: boolean
  error: string | null
  paging: EventLogPaging
  loadOlder: () => Promise<void>
}

export function useEventLog(options: UseEventLogOptions = {}): UseEventLogResult {
  const { location, cluster } = options
  const [error, setError] = useState<string | null>(null)
  const lastSnapshotRef = useRef<{
    source: readonly EventLogEntry[]
    filtered: readonly EventLogEntry[]
    location: string | undefined
    cluster: string | undefined
  } | null>(null)

  useEffect(() => {
    addConnectionRef()
    return () => {
      removeConnectionRef()
    }
  }, [])

  const getSnapshot = useCallback(() => {
    const snapshot = globalEventLogStore.snapshot()
    const sourceEntries = snapshot.entries

    if (location === undefined) {
      return sourceEntries
    }

    if (
      lastSnapshotRef.current &&
      lastSnapshotRef.current.source === sourceEntries &&
      lastSnapshotRef.current.location === location &&
      lastSnapshotRef.current.cluster === cluster
    ) {
      return lastSnapshotRef.current.filtered
    }

    const filtered = sourceEntries.filter((entry) => {
      const room = entry.payload.room
      const entryCluster = entry.payload.cluster
      if (typeof room !== 'string' || room !== location) {
        return false
      }
      if (cluster !== undefined && entryCluster !== cluster) {
        return false
      }
      return true
    })

    lastSnapshotRef.current = {
      source: sourceEntries,
      filtered,
      location,
      cluster,
    }

    return filtered
  }, [location, cluster])

  const entries = useSyncExternalStore(
    (listener) => globalEventLogStore.subscribe(listener),
    getSnapshot,
  )

  useEffect(() => {
    if (error !== null) setError(null)
  }, [location, cluster])

  return {
    entries,
    connected: getActiveConnections() > 0,
    error,
    paging: globalEventLogStore.snapshot().paging,
    loadOlder,
  }
}

export function useEventLogPagination(): Readonly<{
  paging: EventLogPaging
  loadOlder: () => Promise<void>
}> {
  const paging = useSyncExternalStore(
    (listener) => globalEventLogStore.subscribe(listener),
    () => globalEventLogStore.snapshot().paging,
  )
  return { paging, loadOlder }
}

export function _getSharedStoreForTesting() {
  return globalEventLogStore
}

export function _resetSharedStoreForTesting(): void {
  resetTransportState()
  globalEventLogStore.reset()
}
