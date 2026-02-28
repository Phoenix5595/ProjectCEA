import GrafanaPanel from '../components/GrafanaPanel'

/**
 * Flower Room Monitoring page
 * Embeds Grafana dashboard for flower room metrics
 */
export default function FlowerMonitoring() {
  // Placeholder dashboard UID - user will replace with actual UID
  const DASHBOARD_UID = 'flower-monitoring'

  return (
    <div className="min-h-screen bg-surface-base p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold text-text-default mb-6">Flower Room Monitoring</h1>
        
        <GrafanaPanel
          dashboardUid={DASHBOARD_UID}
          title="Flower Room"
          height={600}
        />
        
        {!DASHBOARD_UID || DASHBOARD_UID === 'flower-monitoring' ? (
          <p className="text-text-subtle text-sm mt-4">
            Dashboard not configured. Replace the dashboard UID in FlowerMonitoring.tsx with your Grafana dashboard UID.
          </p>
        ) : null}
      </div>
    </div>
  )
}
