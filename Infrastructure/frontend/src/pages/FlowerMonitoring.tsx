import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

/**
 * Flower Room Monitoring page
 * Embeds 6 individual Grafana panels for flower room metrics
 */
export default function FlowerMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="mx-auto" style={{ maxWidth: '1600px' }}>
        <h1 className="text-2xl font-bold text-text-default mb-6">Flower Room Monitoring</h1>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col lg:flex-row gap-4">
            
            <div className="w-full lg:w-1/6 flex flex-col gap-4">
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={1} title="Averages" height={320} />
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={2} title="Front Cluster" height={320} />
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={3} title="Back Cluster" height={320} />
            </div>

            <div className="w-full lg:w-5/6 flex flex-col gap-4">
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={4} title="Temperature, RH and VPD" height={656} />
              <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={5} title="CO2 and Pressure" height={300} />
            </div>

          </div>

          <div className="w-full">
            <GrafanaPanel dashboardUid={DASHBOARD_UID} panelId={6} title="Statistics - All Sensors" height={320} />
          </div>
        </div>

      </div>
    </div>
  )
}
