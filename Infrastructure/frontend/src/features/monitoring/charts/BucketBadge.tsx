/**
 * Bucket-span badge for a monitoring chart header (R4).
 *
 * Shows the recorded sensor bucket interval so the reader knows every plotted
 * sensor point stands for a bucket rather than one sample. A one-second or
 * unbucketed read is labelled `raw`.
 */

/** Human bucket span: `raw`, `2 s`, `10 s`, `1 min`, `10 min`, `1 h`. */
export function bucketIntervalLabel(intervalSeconds: number | null | undefined): string {
  if (intervalSeconds === null || intervalSeconds === undefined) return 'raw'
  const seconds = Math.floor(intervalSeconds)
  if (!Number.isFinite(seconds) || seconds <= 1) return 'raw'
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) {
    return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`
  }
  if (seconds % 3600 === 0) return `${seconds / 3600} h`
  return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`
}

export interface BucketBadgeProps {
  readonly intervalSeconds: number | null | undefined
}

export function BucketBadge({ intervalSeconds }: BucketBadgeProps) {
  return (
    <span
      className="mon-chart__bucket-badge"
      data-testid="chart-bucket-badge"
      title="Recorded bucket span for sensor points"
      style={{
        position: 'absolute',
        top: 4,
        right: 8,
        zIndex: 2,
        padding: '0 4px',
        borderRadius: 3,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.02em',
        color: 'var(--mon-text-secondary, #c8d6c2)',
        background: 'var(--surface-secondary, #20281c)',
        border: '1px solid var(--border-subtle, #2f3b2a)',
        pointerEvents: 'none',
      }}
    >
      Buckets: {bucketIntervalLabel(intervalSeconds)}
    </span>
  )
}
