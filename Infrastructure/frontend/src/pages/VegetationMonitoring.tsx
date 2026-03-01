import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Vegetation Monitoring page
 * Embeds four Grafana panels in a responsive vertical stack
 */
export default function VegetationMonitoring() {
  // Target Grafana dashboard UID for Vegetation monitoring
  const DASHBOARD_UID = '80bcfd37-f781-48da-aba9-48d3b06a6347'

  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Vegetation Monitoring</h1>

        <div className="flex flex-col gap-4">
          {/* Panel 1: Sensor Values (Table) */}
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={1} title="Sensor Values" />

          {/* Panel 4: Temperature, RH and VPD (Timeseries) */}
          <GrafanaPanel
            dashboardUid={DASHBOARD_UID}
            panelId={4}
            title="Temperature, RH and VPD"
            height={400}
          />

          {/* Panel 5: Pressure and Devices (Timeseries) */}
          <GrafanaPanel
            dashboardUid={DASHBOARD_UID}
            panelId={5}
            title="Pressure and Devices"
            height={400}
          />

          {/* Panel 6: Statistics (Table) */}
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={6} title="Statistics" />
        </div>
      </div>
    </div>
  )
}
