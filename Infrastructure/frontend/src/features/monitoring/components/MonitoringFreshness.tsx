import { formatTimestamp } from './tables/tableFormat'

export interface MonitoringFreshnessProps {
  readonly lastGoodAt: Date | null
  readonly errorAt: Date | null
}

export function MonitoringFreshness({ lastGoodAt = null, errorAt = null }: MonitoringFreshnessProps) {
  if (errorAt === null) return null
  const lastGood = lastGoodAt === null ? 'No successful monitoring range is available.' : `Last good monitoring range: ${formatTimestamp(lastGoodAt)}.`

  return (
    <div className="mon-banner mon-banner--error" role="status" aria-live="polite">
      Data stale. {lastGood} Latest range error: {formatTimestamp(errorAt)}.
    </div>
  )
}
