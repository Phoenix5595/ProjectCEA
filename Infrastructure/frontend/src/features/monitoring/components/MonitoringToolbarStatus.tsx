import type { ToolbarMonitoring } from './TimeRangeToolbar.monitoring'
import { MonitoringStatus } from './MonitoringStatus'

interface MonitoringToolbarStatusProps {
  readonly monitoring: ToolbarMonitoring
}

export function MonitoringToolbarStatus({
  monitoring,
}: MonitoringToolbarStatusProps) {
  return (
    <div className="mon-status">
      <div className="mon-status__summary">
        {monitoring.projectionRevision !== null && (
          <span className="mon-status__meta">Projection {monitoring.projectionRevision}</span>
        )}
        {monitoring.runtimeSnapshotVersion !== null && (
          <span className="mon-status__meta">Runtime {monitoring.runtimeSnapshotVersion}</span>
        )}
      </div>
      <div className="mon-status__actions">
        {monitoring.onRetry !== undefined && (
          <button type="button" onClick={monitoring.onRetry}>Retry</button>
        )}
      </div>
      <MonitoringStatus errors={monitoring.errors} />
    </div>
  )
}
