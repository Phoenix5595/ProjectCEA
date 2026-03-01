import GrafanaPanel from '../components/GrafanaPanel'

export default function FlowerSoil() {
  const DASHBOARD_UID = 'flower-sector-soil'

  return (
    <div className="min-h-screen bg-surface-base p-4 overflow-hidden">
      <div className="max-w-7xl mx-auto" style={{ height: 'calc(100vh - 2rem)' }}>
        <h1 className="text-2xl font-bold text-text-default mb-4">Soil Monitoring</h1>
        
        <div className="h-[calc(100%-4rem)] overflow-hidden">
          <GrafanaPanel
            dashboardUid={DASHBOARD_UID}
            panelId={1}
            title="Soil Metrics"
            height="100%"
          />
        </div>
      </div>
    </div>
  )
}
