import { useCallback, useMemo, useSyncExternalStore } from 'react'

import type { EventBucketCount } from '../features/event-log/presentation/categoryTheme'

/** Window-height breakpoint: eight buckets only for windows ≥ 1440 CSS px tall. */
const DASHBOARD_EVENT_BUCKET_QUERY = '(min-height: 1440px)'

/**
 * Height-adaptive dashboard bucket policy, selected by window height (not
 * screen size and not width): 1440 CSS px and taller windows show the
 * eight-bucket grouped console, shorter windows keep the standing six.
 * Client-only desktop UI: when `window`/`window.matchMedia` is absent the
 * deterministic server snapshot is six.
 */
export function useDashboardEventBucketCount(): EventBucketCount {
  const list = useMemo(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
    return window.matchMedia(DASHBOARD_EVENT_BUCKET_QUERY)
  }, [])
  const subscribe = useCallback(
    (notifyStore: () => void) => {
      if (list === null) return () => undefined
      list.addEventListener('change', notifyStore)
      return () => list.removeEventListener('change', notifyStore)
    },
    [list]
  )
  const getSnapshot = useCallback(() => (list === null ? false : list.matches), [list])

  // No wide window yet reports false; the server snapshot shares that closure.
  const matched = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return matched ? 8 : 6
}
