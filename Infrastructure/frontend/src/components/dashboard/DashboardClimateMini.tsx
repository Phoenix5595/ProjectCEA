import { getClimateDisplay, hasClimateData, renderTemperature } from './dashboardDisplay'
import { clusterFreshnessText, type RoomSensorStatus } from './dashboardStatus'

export function DashboardClimateMini({
  label,
  location,
  cluster,
  sensorData,
  sensorStatus,
  compact = false,
}: {
  label: string
  location: string
  cluster: string
  sensorData: Record<string, number>
  sensorStatus: RoomSensorStatus
  compact?: boolean
}) {
  const unplugged = !hasClimateData(location, cluster, sensorData)
  const alert = sensorStatus.quality === 'stale' || sensorStatus.quality === 'missing'
  const freshness = clusterFreshnessText(sensorStatus)
  const valueClass = alert
    ? 'text-status-danger-text font-mono tabular-nums'
    : 'text-text-default font-mono tabular-nums'

  return (
    <div
      data-testid={`climate-cluster-${cluster}`}
      data-alert={alert ? 'true' : 'false'}
      className={`rounded-sm border p-1.5 cursor-help ${
        alert
          ? 'border-status-danger bg-status-danger/10 text-status-danger-text'
          : 'border-transparent bg-surface-secondary'
      } ${compact ? 'min-w-0' : 'min-w-[9.5rem]'}`}
      title={freshness}
    >
      <span className="sr-only">{freshness}</span>
      <div
        className={`flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-10 ${alert ? 'text-status-danger-text' : 'text-text-muted'}`}
      >
        <span>{label}</span>
        {unplugged && sensorStatus.quality !== 'missing' && (
          <span
            className="text-status-danger cursor-help"
            title={`Climate readings are unavailable for ${label.toLowerCase()} (${cluster}).`}
          >
            ⚠
          </span>
        )}
        <div className="flex min-w-0 flex-wrap gap-x-2 gap-y-0.5">
          <div>
            <span className="text-text-subtle">T </span>
            <span className={valueClass}>{renderTemperature(location, cluster, sensorData)}</span>
          </div>
          <div>
            <span className="text-text-subtle">RH </span>
            <span className={valueClass}>
              {getClimateDisplay(sensorData, location, cluster, 'rh', '%')}
            </span>
          </div>
          <div>
            <span className="text-text-subtle">CO₂ </span>
            <span className={valueClass}>
              {getClimateDisplay(sensorData, location, cluster, 'co2', ' ppm', 0)}
            </span>
          </div>
          <div>
            <span className="text-text-subtle">VPD </span>
            <span className={valueClass}>
              {getClimateDisplay(sensorData, location, cluster, 'vpd', ' kPa')}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
