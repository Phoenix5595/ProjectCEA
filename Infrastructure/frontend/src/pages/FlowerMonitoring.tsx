import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

/**
 * Flower Room Monitoring page
 * Embeds the full Grafana dashboard
 */
export default function FlowerMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="mx-auto h-full flex flex-col" style={{ maxWidth: '1600px', minHeight: 'calc(100vh - 2rem)' }}>
        <h1 className="text-2xl font-bold text-text-default mb-6">Flower Room Monitoring</h1>

        <div className="flex-grow w-full h-full">
          <GrafanaPanel 
            dashboardUid={DASHBOARD_UID} 
            fullDashboard={true} 
            height="calc(100vh - 120px)" 
          />
        </div>
      </div>
    </div>
  )
}
