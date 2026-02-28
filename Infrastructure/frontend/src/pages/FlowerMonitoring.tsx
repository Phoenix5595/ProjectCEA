import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

/**
 * Flower Room Monitoring page
 * Embeds 6 individual Grafana panels for flower room metrics
 */
export default function FlowerMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Flower Room Monitoring</h1>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="md:col-span-2">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={1} title="Averages" height={320} />
          </div>
          <div className="md:col-span-1">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={2} title="Front Cluster" height={320} />
          </div>
          <div className="md:col-span-1">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={3} title="Back Cluster" height={320} />
          </div>
          <div className="md:col-span-2">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={4} title="Temperature, RH and VPD" height={400} />
          </div>
          <div className="md:col-span-2">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={5} title="CO2 and Pressure" height={400} />
          </div>
          <div className="md:col-span-2">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={6} title="Statistics - All Sensors" height={320} />
          </div>
        </div>
      </div>
    </div>
  )
}
