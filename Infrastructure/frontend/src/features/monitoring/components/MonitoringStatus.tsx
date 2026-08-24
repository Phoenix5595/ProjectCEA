/**
 * Monitoring status panel.
 *
 * A pure, prop-driven visualization of per-source health for the monitoring
 * pages. It renders a status summary (normal / loading / stale / error /
 * unavailable), a dismissible banner per distinct error, and optional
 * Retry / Pause / Resume actions. It never couples to store internals and never
 * fabricates control truth.
 */
import { useState } from 'react'
import type { Quality } from '../api'

export interface MonitoringStatusProps {
  errors: string[]
  tailLoading: boolean
  reconciling: boolean
  anchorQuality: Quality | null
  projectionRevision: string | null
  runtimeSnapshotVersion: number | null
  isLive: boolean
  onRetry?: () => void
  onPause?: () => void
  onResume?: () => void
}

type StatusKind = 'normal' | 'loading' | 'stale' | 'error' | 'unavailable' | 'reconciling'

interface StatusInput {
  errors: string[]
  tailLoading: boolean
  reconciling: boolean
  anchorQuality: Quality | null
}

const STATUS_LABEL: Record<StatusKind, string> = {
  normal: 'Normal',
  loading: 'Loading',
  stale: 'Stale',
  error: 'Error',
  unavailable: 'Unavailable',
  reconciling: 'Reconciling',
}

function statusKind(props: StatusInput): StatusKind {
  if (props.reconciling) return 'reconciling'
  if (props.errors.length > 0) return 'error'
  if (props.tailLoading) return 'loading'
  if (props.anchorQuality === 'unavailable') return 'unavailable'
  if (props.anchorQuality === 'estimated') return 'stale'
  return 'normal'
}

export function MonitoringStatus({
  errors,
  tailLoading,
  reconciling,
  anchorQuality,
  projectionRevision,
  runtimeSnapshotVersion,
  isLive,
  onRetry,
  onPause,
  onResume,
}: MonitoringStatusProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const visibleErrors = errors.filter((err) => !dismissed.has(err))
  const kind = statusKind({ errors, tailLoading, reconciling, anchorQuality })

  const dismiss = (err: string): void => {
    setDismissed((prev) => new Set(prev).add(err))
  }

  return (
    <div className="mon-status">
      <div className="mon-status__summary" role="status">
        <span className={`mon-status__badge mon-status__badge--${kind}`}>
          {STATUS_LABEL[kind]}
        </span>
        {projectionRevision !== null && (
          <span className="mon-status__meta">Projection {projectionRevision}</span>
        )}
        {runtimeSnapshotVersion !== null && (
          <span className="mon-status__meta">Runtime v{runtimeSnapshotVersion}</span>
        )}
        {isLive && <span className="mon-status__meta">Live</span>}
      </div>

      {visibleErrors.map((err) => (
        <div key={err} role="alert" className="mon-banner mon-banner--error">
          <span className="mon-status__error-text">{err}</span>
          <button
            type="button"
            className="mon-status__dismiss"
            aria-label="Dismiss error"
            onClick={() => dismiss(err)}
          >
            ×
          </button>
        </div>
      ))}

      {(onRetry || onPause || onResume) && (
        <div className="mon-status__actions">
          {onRetry && (
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          )}
          {onPause && (
            <button type="button" onClick={onPause}>
              Pause
            </button>
          )}
          {onResume && (
            <button type="button" onClick={onResume}>
              Resume
            </button>
          )}
        </div>
      )}
    </div>
  )
}
