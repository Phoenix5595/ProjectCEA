import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Vegetation Room Monitoring page
 * Embeds Grafana dashboard for vegetation room metrics
 */
export default function VegetationMonitoring() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'veg-monitoring'

  return (
    <div className="min-h-screen bg-gray-950 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">Vegetation Room Monitoring</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Vegetation Room"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'veg-monitoring' ? (
          <p className="text-gray-500 text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in VegetationMonitoring.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
