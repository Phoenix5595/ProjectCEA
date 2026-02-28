import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Flower Room Monitoring page
 * Embeds Grafana dashboard for flower room metrics
 */
export default function FlowerMonitoring() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'flower-monitoring'

  return (
    <div className="min-h-screen bg-gray-950 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-gray-100 mb-6">Flower Room Monitoring</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Flower Room"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'flower-monitoring' ? (
          <p className="text-gray-500 text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in FlowerMonitoring.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
