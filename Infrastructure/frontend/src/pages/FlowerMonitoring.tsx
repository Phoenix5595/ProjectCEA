import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '7467103e-9964-4e06-9fc8-c43610129ba9'

export default function FlowerMonitoring() {
  return (
    <div className="h-screen bg-surface-base overflow-hidden">
      <div className="h-full flex flex-col">
        <h1 className="text-lg font-bold text-text-default px-2 py-1 shrink-0">Flower Room Monitoring</h1>

        <div className="flex-grow overflow-hidden">
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
