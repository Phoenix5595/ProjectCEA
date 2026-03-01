import GrafanaPanel from '../components/GrafanaPanel'

export default function FlowerSoil() {
  const DASHBOARD_UID = 'flower-sector-soil'

  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Soil Monitoring</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Flower Soil"
          height={600}
        />
      </div>
    </div>
  )
}
