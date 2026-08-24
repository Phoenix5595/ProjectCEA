import type { Quality } from '../api'

export interface ToolbarMonitoring {
  readonly errors: string[]
  readonly tailLoading: boolean
  readonly reconciling: boolean
  readonly anchorQuality: Quality | null
  readonly projectionRevision: string | null
  readonly runtimeSnapshotVersion: number | null
  readonly onRetry?: () => void
}
