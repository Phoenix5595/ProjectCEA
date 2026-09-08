/**
 * Monitoring status panel.
 *
 * A pure, prop-driven visualization of per-source health for the monitoring
 * pages. The toolbar renders the concise status badge while this component
 * retains detailed, dismissible error alerts below it.
 */
import { useState } from 'react'
import type { Quality } from '../api'

export interface MonitoringStatusProps {
  errors: string[]
}

type StatusKind = 'normal' | 'loading' | 'stale' | 'error' | 'unavailable' | 'reconciling'

export interface MonitoringStatusBadgeProps {
  readonly errors: string[]
  readonly tailLoading: boolean
  readonly reconciling: boolean
  readonly anchorQuality: Quality | null
}

const STATUS_LABEL: Record<StatusKind, string> = {
  normal: 'Normal',
  loading: 'Loading',
  stale: 'Stale',
  error: 'Error',
  unavailable: 'Unavailable',
  reconciling: 'Reconciling',
}

function statusKind(props: MonitoringStatusBadgeProps): StatusKind {
  if (props.reconciling) return 'reconciling'
  if (props.errors.length > 0) return 'error'
  if (props.tailLoading) return 'loading'
  if (props.anchorQuality === 'unavailable') return 'unavailable'
  if (props.anchorQuality === 'estimated') return 'stale'
  return 'normal'
}

export function MonitoringStatus({
  errors,
}: MonitoringStatusProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const visibleErrors = errors.filter((err) => !dismissed.has(err))

  const dismiss = (err: string): void => {
    setDismissed((prev) => new Set(prev).add(err))
  }

  return (
    <div className="mon-status">
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
    </div>
  )
}

export function MonitoringStatusBadge(props: MonitoringStatusBadgeProps) {
  const kind = statusKind(props)
  return (
    <span className={`mon-status__badge mon-status__badge--${kind}`} role="status">
      {STATUS_LABEL[kind]}
    </span>
  )
}
