import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

export default function FlowerMonitoring() {
  return (
    <div className="min-h-screen bg-surface-base p-4 overflow-hidden">
      <div className="mx-auto h-full" style={{ maxWidth: '1600px', height: 'calc(100vh - 2rem)' }}>
        <h1 className="text-2xl font-bold text-text-default mb-4">Flower Room Monitoring</h1>

        <div className="h-[calc(100%-4rem)] overflow-hidden">
          <GrafanaPanel 
            dashboardUid={DASHBOARD_UID} 
            timeRange={true}
            height="100%" 
          />
        </div>
      </div>
    </div>
  )
}
