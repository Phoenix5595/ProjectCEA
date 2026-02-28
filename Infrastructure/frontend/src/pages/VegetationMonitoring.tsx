import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '80bcfd37-f781-48da-aba9-48d3b06a6347'

/**
 * Vegetation Room Monitoring page
 * Embeds 4 individual Grafana panels for vegetation room metrics
 */
export default function VegetationMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Vegetation Room Monitoring</h1>

        <div className="flex flex-col gap-4">
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={1} title="Sensor Values" height={320} />
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={4} title="Temperature, RH and VPD" height={400} />
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={5} title="Pressure and Devices" height={400} />
          <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={6} title="Statistics" height={320} />
        </div>
      </div>
    </div>
  )
}
