import GrafanaPanel from '../components/GrafanaPanel'

const DASHBOARD_UID = '80bcfd37-f781-48da-aba9-48d3b06a6347'

export default function VegetationMonitoring() {
  return (
    <div className="bg-surface-base overflow-hidden -mt-4 -mb-4" style={{ height: 'calc(100vh - 60px)' }}>
      <div className="h-full flex flex-col">
        <h1 className="text-lg font-bold text-text-default px-2 py-1 shrink-0">Vegetation Room Monitoring</h1>

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
